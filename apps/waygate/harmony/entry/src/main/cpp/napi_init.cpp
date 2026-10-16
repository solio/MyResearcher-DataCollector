// Waygate native bridge: phone-side SSH client built on libssh2 (mbedTLS backend).
//
// Scope of this slice: host-key probe, authenticated connect, exec channel with
// JSON stdin + EOF, and close. direct-tcpip forwarding is added in the next slice.
#include <napi/native_api.h>

#undef LOG_DOMAIN
#undef LOG_TAG
#define LOG_DOMAIN 0x3200
#define LOG_TAG "waygate_ssh"
#include <hilog/log.h>

#include <libssh2.h>

#include <arpa/inet.h>
#include <netdb.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include <cerrno>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <poll.h>
#include <atomic>
#include <functional>
#include <map>
#include <mutex>
#include <string>
#include <thread>

namespace {

std::once_flag gLibInit;
std::mutex gLock;  // libssh2 sessions are not thread safe; serialise every call.

// Every blocking wait is bounded. Without these the phone had no way to fail: a
// throttled banner, a stalled exec or an unreachable forward endpoint hung forever.
constexpr int kConnectTimeoutMs = 10000;
constexpr int kHandshakeTimeoutMs = 20000;
constexpr int kAuthTimeoutMs = 20000;
constexpr int kExecTimeoutMs = 60000;
constexpr int kChannelOpenTimeoutMs = 15000;
constexpr int kChannelWriteTimeoutMs = 30000;

struct Session {
    int fd = -1;
    LIBSSH2_SESSION* session = nullptr;
};

std::map<int, Session*> gSessions;
int gNextId = 1;

const char kB64[] =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

std::string Base64(const unsigned char* data, size_t len) {
    std::string out;
    out.reserve((len + 2) / 3 * 4);
    for (size_t i = 0; i < len; i += 3) {
        unsigned v = static_cast<unsigned>(data[i]) << 16;
        if (i + 1 < len) v |= static_cast<unsigned>(data[i + 1]) << 8;
        if (i + 2 < len) v |= static_cast<unsigned>(data[i + 2]);
        out.push_back(kB64[(v >> 18) & 63]);
        out.push_back(kB64[(v >> 12) & 63]);
        out.push_back(i + 1 < len ? kB64[(v >> 6) & 63] : '=');
        out.push_back(i + 2 < len ? kB64[v & 63] : '=');
    }
    return out;
}

// ssh-keygen and openssh print SHA256 host key fingerprints as base64 without
// padding. Emitting the same text lets the operator compare the phone's TOFU
// screen against `ssh-keygen -lf` directly.
std::string Sha256Fingerprint(const unsigned char* data, size_t len) {
    std::string encoded = Base64(data, len);
    while (!encoded.empty() && encoded.back() == '=') encoded.pop_back();
    return "SHA256:" + encoded;
}

std::string HostKeyTypeName(int type) {
    switch (type) {
        case LIBSSH2_HOSTKEY_TYPE_RSA: return "ssh-rsa";
        case LIBSSH2_HOSTKEY_TYPE_ECDSA_256: return "ecdsa-sha2-nistp256";
        case LIBSSH2_HOSTKEY_TYPE_ECDSA_384: return "ecdsa-sha2-nistp384";
        case LIBSSH2_HOSTKEY_TYPE_ECDSA_521: return "ecdsa-sha2-nistp521";
        case LIBSSH2_HOSTKEY_TYPE_ED25519: return "ssh-ed25519";
        default: return "unknown";
    }
}

bool ReadString(napi_env env, napi_value object, const char* name, std::string& out) {
    napi_value value = nullptr;
    if (napi_get_named_property(env, object, name, &value) != napi_ok || value == nullptr) {
        return false;
    }
    napi_valuetype kind = napi_undefined;
    if (napi_typeof(env, value, &kind) != napi_ok || kind != napi_string) {
        return false;
    }
    size_t length = 0;
    if (napi_get_value_string_utf8(env, value, nullptr, 0, &length) != napi_ok) {
        return false;
    }
    out.resize(length);
    size_t written = 0;
    return napi_get_value_string_utf8(env, value, out.data(), length + 1, &written) == napi_ok;
}

int ReadInt(napi_env env, napi_value object, const char* name, int fallback) {
    napi_value value = nullptr;
    if (napi_get_named_property(env, object, name, &value) != napi_ok || value == nullptr) {
        return fallback;
    }
    int32_t number = fallback;
    if (napi_get_value_int32(env, value, &number) != napi_ok) {
        return fallback;
    }
    return number;
}

bool HasProperty(napi_env env, napi_value object, const char* name) {
    bool has = false;
    napi_has_named_property(env, object, name, &has);
    return has;
}

int TcpConnect(const std::string& host, int port, std::string& error) {
    char service[16];
    snprintf(service, sizeof(service), "%d", port);
    struct addrinfo hints;
    memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    struct addrinfo* list = nullptr;
    if (getaddrinfo(host.c_str(), service, &hints, &list) != 0) {
        error = "无法解析 SSH 主机地址";
        return -1;
    }
    int fd = -1;
    for (struct addrinfo* item = list; item != nullptr; item = item->ai_next) {
        fd = socket(item->ai_family, item->ai_socktype, item->ai_protocol);
        if (fd < 0) continue;
        // Bounded connect: a throttled or firewalled host must not hold this call, and
        // through it the shared session lock, indefinitely.
        int flags = fcntl(fd, F_GETFL, 0);
        fcntl(fd, F_SETFL, flags | O_NONBLOCK);
        int result = connect(fd, item->ai_addr, item->ai_addrlen);
        if (result != 0 && errno == EINPROGRESS) {
            struct pollfd waiting;
            waiting.fd = fd;
            waiting.events = POLLOUT;
            waiting.revents = 0;
            if (poll(&waiting, 1, kConnectTimeoutMs) > 0) {
                int pending = 0;
                socklen_t length = sizeof(pending);
                if (getsockopt(fd, SOL_SOCKET, SO_ERROR, &pending, &length) == 0 && pending == 0) {
                    result = 0;
                } else {
                    result = -1;
                }
            } else {
                result = -1;
            }
        }
        if (flags >= 0) fcntl(fd, F_SETFL, flags);
        if (result == 0) break;
        close(fd);
        fd = -1;
    }
    freeaddrinfo(list);
    if (fd < 0) error = "无法连接该 SSH 主机";
    return fd;
}

void CloseSession(Session* entry) {
    if (entry->session != nullptr) {
        libssh2_session_disconnect(entry->session, "waygate close");
        libssh2_session_free(entry->session);
        entry->session = nullptr;
    }
    if (entry->fd >= 0) {
        close(entry->fd);
        entry->fd = -1;
    }
    delete entry;
}

// ---- async plumbing -------------------------------------------------------

struct Work {
    napi_deferred deferred = nullptr;
    napi_async_work async = nullptr;
    std::string error;
    std::function<void()> run;
    std::function<napi_value(napi_env)> build;
};

void RunWork(napi_env env, void* data) {
    static_cast<Work*>(data)->run();
}

void SettleWork(napi_env env, napi_status status, void* data) {
    Work* work = static_cast<Work*>(data);
    if (!work->error.empty()) {
        OH_LOG_ERROR(LOG_APP, "waygate_ssh failed: %{public}s", work->error.c_str());
        napi_value message = nullptr;
        napi_create_string_utf8(env, work->error.c_str(), NAPI_AUTO_LENGTH, &message);
        napi_value errorObject = nullptr;
        napi_create_error(env, nullptr, message, &errorObject);
        napi_reject_deferred(env, work->deferred, errorObject);
    } else {
        napi_value result = work->build(env);
        napi_resolve_deferred(env, work->deferred, result);
    }
    napi_delete_async_work(env, work->async);
    delete work;
}

napi_value StartAsync(napi_env env, Work* work) {
    napi_value promise = nullptr;
    napi_create_promise(env, &work->deferred, &promise);
    napi_value name = nullptr;
    napi_create_string_utf8(env, "waygate_ssh", NAPI_AUTO_LENGTH, &name);
    napi_create_async_work(env, nullptr, name, RunWork, SettleWork, work, &work->async);
    napi_queue_async_work(env, work->async);
    return promise;
}

napi_value MakeString(napi_env env, const std::string& value) {
    napi_value out = nullptr;
    napi_create_string_utf8(env, value.c_str(), value.length(), &out);
    return out;
}

struct HostKey {
    std::string type;
    std::string base64;
    std::string fingerprint;
};

bool GrabHostKey(LIBSSH2_SESSION* session, HostKey& out) {
    size_t length = 0;
    int type = 0;
    const char* key = libssh2_session_hostkey(session, &length, &type);
    if (key == nullptr || length == 0) return false;
    out.type = HostKeyTypeName(type);
    out.base64 = Base64(reinterpret_cast<const unsigned char*>(key), length);
    const char* digest = libssh2_hostkey_hash(session, LIBSSH2_HOSTKEY_HASH_SHA256);
    if (digest != nullptr) {
        out.fingerprint = Sha256Fingerprint(reinterpret_cast<const unsigned char*>(digest), 32);
    }
    return true;
}

}  // namespace

// waygate_ssh.probeHostKey({host, port}) -> {type, base64, fingerprint}
static napi_value ProbeHostKey(napi_env env, napi_callback_info info) {
    size_t argc = 1;
    napi_value argv[1];
    napi_get_cb_info(env, info, &argc, argv, nullptr, nullptr);
    if (argc < 1) {
        napi_throw_type_error(env, nullptr, "需要连接参数对象");
        return nullptr;
    }
    auto* work = new Work();
    std::string host;
    int port = ReadInt(env, argv[0], "port", 22);
    if (!ReadString(env, argv[0], "host", host) || host.empty()) {
        delete work;
        napi_throw_type_error(env, nullptr, "缺少 host");
        return nullptr;
    }
    HostKey* key = new HostKey();
    work->run = [host, port, key, work]() {
        OH_LOG_INFO(LOG_APP, "probeHostKey start host=%{public}s port=%{public}d", host.c_str(), port);
        std::lock_guard<std::mutex> guard(gLock);
        std::call_once(gLibInit, []() { libssh2_init(0); });
        int fd = TcpConnect(host, port, work->error);
        if (fd < 0) return;
        LIBSSH2_SESSION* session = libssh2_session_init();
        if (session == nullptr) {
            close(fd);
            work->error = "无法初始化 SSH 会话";
            return;
        }
        libssh2_session_set_timeout(session, kHandshakeTimeoutMs);
        if (libssh2_session_handshake(session, fd) != 0) {
            libssh2_session_free(session);
            close(fd);
            work->error = "SSH 握手失败";
            return;
        }
        if (!GrabHostKey(session, *key)) {
            work->error = "无法读取该主机指纹";
        }
        libssh2_session_disconnect(session, "probe");
        libssh2_session_free(session);
        close(fd);
    };
    work->build = [key](napi_env e) {
        OH_LOG_INFO(LOG_APP, "probeHostKey ok type=%{public}s fingerprint=%{public}s",
                    key->type.c_str(), key->fingerprint.c_str());
        napi_value out = nullptr;
        napi_create_object(e, &out);
        napi_set_named_property(e, out, "type", MakeString(e, key->type));
        napi_set_named_property(e, out, "base64", MakeString(e, key->base64));
        napi_set_named_property(e, out, "fingerprint", MakeString(e, key->fingerprint));
        delete key;
        return out;
    };
    return StartAsync(env, work);
}

// waygate_ssh.connect({host, port, user, password?, privateKey?, passphrase?,
//                      knownHostType?, knownHostBase64?}) -> {id, type, base64, fingerprint}
static napi_value Connect(napi_env env, napi_callback_info info) {
    size_t argc = 1;
    napi_value argv[1];
    napi_get_cb_info(env, info, &argc, argv, nullptr, nullptr);
    if (argc < 1) {
        napi_throw_type_error(env, nullptr, "需要连接参数对象");
        return nullptr;
    }
    std::string host;
    std::string user;
    std::string password;
    std::string privateKey;
    std::string passphrase;
    std::string knownType;
    std::string knownBase64;
    if (!ReadString(env, argv[0], "host", host) || host.empty()) {
        napi_throw_type_error(env, nullptr, "缺少 host");
        return nullptr;
    }
    ReadString(env, argv[0], "user", user);
    ReadString(env, argv[0], "password", password);
    ReadString(env, argv[0], "privateKey", privateKey);
    ReadString(env, argv[0], "passphrase", passphrase);
    bool hasKnown = ReadString(env, argv[0], "knownHostBase64", knownBase64) && !knownBase64.empty();
    ReadString(env, argv[0], "knownHostType", knownType);
    int port = ReadInt(env, argv[0], "port", 22);

    auto* work = new Work();
    auto* key = new HostKey();
    auto* newId = new int(-1);
    work->run = [=]() {
        std::lock_guard<std::mutex> guard(gLock);
        std::call_once(gLibInit, []() { libssh2_init(0); });
        int fd = TcpConnect(host, port, work->error);
        if (fd < 0) return;
        LIBSSH2_SESSION* session = libssh2_session_init();
        if (session == nullptr) {
            close(fd);
            work->error = "无法初始化 SSH 会话";
            return;
        }
        libssh2_session_set_blocking(session, 1);
        libssh2_session_set_timeout(session, kHandshakeTimeoutMs);
        if (libssh2_session_handshake(session, fd) != 0) {
            libssh2_session_free(session);
            close(fd);
            work->error = "SSH 握手失败";
            return;
        }
        if (!GrabHostKey(session, *key)) {
            libssh2_session_free(session);
            close(fd);
            work->error = "无法读取该主机指纹";
            return;
        }
        if (hasKnown && key->base64 != knownBase64) {
            libssh2_session_disconnect(session, "host key changed");
            libssh2_session_free(session);
            close(fd);
            work->error = "该主机指纹与已保存的不一致，请核对后再连接";
            return;
        }
        int auth = -1;
        if (!privateKey.empty()) {
            auth = libssh2_userauth_publickey_frommemory(
                session, user.c_str(), user.length(), nullptr, 0, privateKey.c_str(),
                privateKey.length(), passphrase.empty() ? nullptr : passphrase.c_str());
        } else if (!password.empty()) {
            auth = libssh2_userauth_password(session, user.c_str(), password.c_str());
        } else {
            work->error = "缺少 SSH 认证方式";
        }
        if (auth != 0) {
            if (work->error.empty()) work->error = "SSH 认证失败，请检查用户名或凭据";
            libssh2_session_disconnect(session, "auth failed");
            libssh2_session_free(session);
            close(fd);
            return;
        }
        // Later exec calls on this session are bounded too, so a worker command that
        // never exits cannot hold the shared lock forever.
        libssh2_session_set_timeout(session, kExecTimeoutMs);
        int handle = gNextId++;
        Session* entry = new Session();
        entry->fd = fd;
        entry->session = session;
        gSessions[handle] = entry;
        *newId = handle;
    };
    work->build = [key, newId](napi_env e) {
        napi_value out = nullptr;
        napi_create_object(e, &out);
        napi_set_named_property(e, out, "id", [e, newId]() {
            napi_value v = nullptr;
            napi_create_int32(e, *newId, &v);
            return v;
        }());
        napi_set_named_property(e, out, "type", MakeString(e, key->type));
        napi_set_named_property(e, out, "base64", MakeString(e, key->base64));
        napi_set_named_property(e, out, "fingerprint", MakeString(e, key->fingerprint));
        delete key;
        delete newId;
        return out;
    };
    return StartAsync(env, work);
}

// waygate_ssh.exec(id, command, stdin) -> {stdout, stderr, exitStatus}
static napi_value Exec(napi_env env, napi_callback_info info) {
    size_t argc = 3;
    napi_value argv[3];
    napi_get_cb_info(env, info, &argc, argv, nullptr, nullptr);
    if (argc < 3) {
        napi_throw_type_error(env, nullptr, "需要 id、命令与 stdin");
        return nullptr;
    }
    int32_t id = -1;
    napi_get_value_int32(env, argv[0], &id);
    std::string command;
    std::string stdin;
    {
        size_t length = 0;
        napi_get_value_string_utf8(env, argv[1], nullptr, 0, &length);
        command.resize(length);
        napi_get_value_string_utf8(env, argv[1], command.data(), length + 1, &length);
        napi_get_value_string_utf8(env, argv[2], nullptr, 0, &length);
        stdin.resize(length);
        napi_get_value_string_utf8(env, argv[2], stdin.data(), length + 1, &length);
    }
    auto* work = new Work();
    auto* stdoutText = new std::string();
    auto* stderrText = new std::string();
    auto* exitStatus = new int(-1);
    work->run = [=]() {
        std::lock_guard<std::mutex> guard(gLock);
        auto found = gSessions.find(id);
        if (found == gSessions.end()) {
            work->error = "SSH 会话不存在或已关闭";
            return;
        }
        LIBSSH2_SESSION* session = found->second->session;
        LIBSSH2_CHANNEL* channel = libssh2_channel_open_session(session);
        if (channel == nullptr) {
            work->error = "无法打开 SSH exec 通道";
            return;
        }
        if (libssh2_channel_exec(channel, command.c_str()) != 0) {
            libssh2_channel_free(channel);
            work->error = "无法在该主机执行验证命令";
            return;
        }
        size_t offset = 0;
        char buffer[8192];
        const size_t kLimit = 4 * 1024 * 1024;
        while (offset < stdin.length()) {
            size_t chunk = stdin.length() - offset;
            if (chunk > sizeof(buffer)) chunk = sizeof(buffer);
            ssize_t written = libssh2_channel_write(channel, stdin.data() + offset, chunk);
            if (written < 0) {
                libssh2_channel_free(channel);
                work->error = "向验证通道写入数据失败";
                return;
            }
            offset += static_cast<size_t>(written);
        }
        libssh2_channel_send_eof(channel);
        while (true) {
            ssize_t read = libssh2_channel_read(channel, buffer, sizeof(buffer));
            if (read > 0) {
                if (stdoutText->size() + static_cast<size_t>(read) > kLimit) {
                    libssh2_channel_free(channel);
                    work->error = "验证命令输出过大";
                    return;
                }
                stdoutText->append(buffer, static_cast<size_t>(read));
                continue;
            }
            if (read == 0) break;
            if (read != LIBSSH2_ERROR_EAGAIN) {
                work->error = "读取验证命令输出失败";
                libssh2_channel_free(channel);
                return;
            }
        }
        while (true) {
            ssize_t read = libssh2_channel_read_stderr(channel, buffer, sizeof(buffer));
            if (read > 0) {
                if (stderrText->size() + static_cast<size_t>(read) > kLimit) break;
                stderrText->append(buffer, static_cast<size_t>(read));
                continue;
            }
            break;
        }
        libssh2_channel_close(channel);
        *exitStatus = libssh2_channel_get_exit_status(channel);
        libssh2_channel_free(channel);
    };
    work->build = [stdoutText, stderrText, exitStatus](napi_env e) {
        napi_value out = nullptr;
        napi_create_object(e, &out);
        napi_set_named_property(e, out, "stdout", MakeString(e, *stdoutText));
        napi_set_named_property(e, out, "stderr", MakeString(e, *stderrText));
        napi_value status = nullptr;
        napi_create_int32(e, *exitStatus, &status);
        napi_set_named_property(e, out, "exitStatus", status);
        delete stdoutText;
        delete stderrText;
        delete exitStatus;
        return out;
    };
    return StartAsync(env, work);
}

// waygate_ssh.close(id) -> true
static napi_value Close(napi_env env, napi_callback_info info) {
    size_t argc = 1;
    napi_value argv[1];
    napi_get_cb_info(env, info, &argc, argv, nullptr, nullptr);
    int32_t id = -1;
    if (argc >= 1) napi_get_value_int32(env, argv[0], &id);
    auto* work = new Work();
    work->run = [id, work]() {
        std::lock_guard<std::mutex> guard(gLock);
        auto found = gSessions.find(id);
        if (found == gSessions.end()) return;
        Session* entry = found->second;
        gSessions.erase(found);
        CloseSession(entry);
    };
    work->build = [](napi_env e) {
        napi_value out = nullptr;
        napi_get_boolean(e, true, &out);
        return out;
    };
    return StartAsync(env, work);
}

// ---- direct-tcpip forwarding -------------------------------------------------
// Mirrors the desktop flow's separate `ssh -L` process: a second SSH session is
// dedicated to forwarding so browser traffic never serialises behind exec calls.
// The session runs non-blocking and every libssh2 call is taken under this
// forward's own lock, so several browser sockets can share it.

struct Forward {
    int listenFd = -1;
    int port = 0;
    int sshFd = -1;
    LIBSSH2_SESSION* session = nullptr;
    std::string host;
    int hostPort = 0;
    std::mutex lock;
    std::atomic<bool> stopped{false};
    std::thread acceptor;
};

std::map<int, Forward*> gForwards;
int gNextForwardId = 1;

void PumpConnection(Forward* forward, int clientFd) {
    LIBSSH2_CHANNEL* channel = nullptr;
    // This session is non-blocking, so a channel open returns EAGAIN until the worker's
    // sshd confirms it. Calling it once (the previous behaviour) refused every browser
    // connection, which is why the shared-exit check never got its page loaded.
    auto openDeadline = std::chrono::steady_clock::now() +
                        std::chrono::milliseconds(kChannelOpenTimeoutMs);
    while (!forward->stopped.load() && std::chrono::steady_clock::now() < openDeadline) {
        {
            std::lock_guard<std::mutex> guard(forward->lock);
            channel = libssh2_channel_direct_tcpip_ex(forward->session, forward->host.c_str(),
                                                      forward->hostPort, "127.0.0.1", 0);
            if (channel != nullptr) break;
            if (libssh2_session_last_errno(forward->session) != LIBSSH2_ERROR_EAGAIN) break;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    if (channel == nullptr) {
        OH_LOG_ERROR(LOG_APP, "direct-tcpip channel open failed host=%{public}s port=%{public}d",
                     forward->host.c_str(), forward->hostPort);
        close(clientFd);
        return;
    }
    OH_LOG_INFO(LOG_APP, "direct-tcpip channel open host=%{public}s port=%{public}d",
                forward->host.c_str(), forward->hostPort);
    char buffer[16384];
    while (!forward->stopped.load()) {
        fd_set readSet;
        FD_ZERO(&readSet);
        FD_SET(clientFd, &readSet);
        struct timeval wait;
        wait.tv_sec = 0;
        wait.tv_usec = 20000;
        int ready = select(clientFd + 1, &readSet, nullptr, nullptr, &wait);
        if (ready > 0) {
            ssize_t got = recv(clientFd, buffer, sizeof(buffer), 0);
            if (got <= 0) break;
            ssize_t offset = 0;
            auto writeDeadline = std::chrono::steady_clock::now() +
                                 std::chrono::milliseconds(kChannelWriteTimeoutMs);
            while (offset < got) {
                {
                    std::lock_guard<std::mutex> guard(forward->lock);
                    ssize_t written = libssh2_channel_write(channel, buffer + offset, got - offset);
                    if (written > 0) { offset += written; continue; }
                    if (written != LIBSSH2_ERROR_EAGAIN) { offset = -1; break; }
                }
                // EAGAIN on a non-blocking session means the socket needs to drain; it is
                // not fatal, but it must not spin forever either.
                if (std::chrono::steady_clock::now() >= writeDeadline) { offset = -1; break; }
                std::this_thread::sleep_for(std::chrono::milliseconds(5));
            }
            if (offset < 0) break;
        }
        ssize_t available = 0;
        {
            std::lock_guard<std::mutex> guard(forward->lock);
            available = libssh2_channel_read(channel, buffer, sizeof(buffer));
            if (available == LIBSSH2_ERROR_EAGAIN) available = 0;
            if (libssh2_channel_eof(channel) == 1) available = -1;
        }
        if (available < 0) break;
        if (available > 0) {
            ssize_t offset = 0;
            while (offset < available) {
                ssize_t sent = send(clientFd, buffer + offset, available - offset, 0);
                if (sent <= 0) { offset = -1; break; }
                offset += sent;
            }
            if (offset < 0) break;
        }
    }
    {
        std::lock_guard<std::mutex> guard(forward->lock);
        libssh2_channel_close(channel);
        libssh2_channel_free(channel);
    }
    close(clientFd);
}

// waygate_ssh.forward({host, port, user, password?/privateKey?/passphrase?,
//                      knownHostBase64?, remoteHost, remotePort}) -> {id, port}
static napi_value ForwardStart(napi_env env, napi_callback_info info) {
    size_t argc = 1;
    napi_value argv[1];
    napi_get_cb_info(env, info, &argc, argv, nullptr, nullptr);
    if (argc < 1) {
        napi_throw_type_error(env, nullptr, "需要转发参数对象");
        return nullptr;
    }
    std::string host, user, password, privateKey, passphrase, knownBase64, remoteHost;
    if (!ReadString(env, argv[0], "host", host) || host.empty() ||
        !ReadString(env, argv[0], "remoteHost", remoteHost) || remoteHost.empty()) {
        napi_throw_type_error(env, nullptr, "缺少 host 或 remoteHost");
        return nullptr;
    }
    ReadString(env, argv[0], "user", user);
    ReadString(env, argv[0], "password", password);
    ReadString(env, argv[0], "privateKey", privateKey);
    ReadString(env, argv[0], "passphrase", passphrase);
    ReadString(env, argv[0], "knownHostBase64", knownBase64);
    int port = ReadInt(env, argv[0], "port", 22);
    int remotePort = ReadInt(env, argv[0], "remotePort", 0);

    auto* work = new Work();
    auto* created = new Forward();
    auto* newId = new int(-1);
    created->host = remoteHost;
    created->hostPort = remotePort;
    work->run = [=]() {
        // Deliberately outside gLock: this session never enters gSessions, only this
        // forward's own threads touch it, and a stalled handshake here must not block
        // the exec session the app is still using.
        std::call_once(gLibInit, []() { libssh2_init(0); });
        created->sshFd = TcpConnect(host, port, work->error);
        if (created->sshFd < 0) return;
        created->session = libssh2_session_init();
        if (created->session == nullptr) {
            close(created->sshFd);
            created->sshFd = -1;
            work->error = "无法初始化 SSH 转发会话";
            return;
        }
        libssh2_session_set_blocking(created->session, 0);
        auto handshakeDeadline = std::chrono::steady_clock::now() +
                                 std::chrono::milliseconds(kHandshakeTimeoutMs);
        int handshake = 0;
        while ((handshake = libssh2_session_handshake(created->session, created->sshFd)) ==
               LIBSSH2_ERROR_EAGAIN) {
            if (std::chrono::steady_clock::now() >= handshakeDeadline) {
                work->error = "SSH 转发会话握手超时";
                return;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
        if (handshake != 0) {
            work->error = "SSH 转发会话握手失败";
            return;
        }
        HostKey key;
        if (!GrabHostKey(created->session, key) ||
            (!knownBase64.empty() && key.base64 != knownBase64)) {
            work->error = "该主机指纹与已保存的不一致，已停止转发";
            return;
        }
        int auth = -1;
        auto attempt = [&]() {
            if (auth == 0 || auth == LIBSSH2_ERROR_EAGAIN) return;
            if (!privateKey.empty()) {
                auth = libssh2_userauth_publickey_frommemory(
                    created->session, user.c_str(), user.length(), nullptr, 0,
                    privateKey.c_str(), privateKey.length(),
                    passphrase.empty() ? nullptr : passphrase.c_str());
            } else if (!password.empty()) {
                auth = libssh2_userauth_password(created->session, user.c_str(), password.c_str());
            }
        };
        auto authDeadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(kAuthTimeoutMs);
        while (true) {
            attempt();
            if (auth == 0) break;
            if (auth != LIBSSH2_ERROR_EAGAIN) {
                work->error = "SSH 认证失败，请检查用户名或凭据";
                return;
            }
            if (std::chrono::steady_clock::now() >= authDeadline) {
                work->error = "SSH 转发会话认证超时";
                return;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
        created->listenFd = socket(AF_INET, SOCK_STREAM, 0);
        if (created->listenFd < 0) {
            work->error = "无法创建本地转发端口";
            return;
        }
        struct sockaddr_in local;
        memset(&local, 0, sizeof(local));
        local.sin_family = AF_INET;
        local.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        local.sin_port = 0;
        if (bind(created->listenFd, reinterpret_cast<struct sockaddr*>(&local), sizeof(local)) != 0 ||
            listen(created->listenFd, 16) != 0) {
            close(created->listenFd);
            created->listenFd = -1;
            work->error = "无法监听本地转发端口";
            return;
        }
        socklen_t length = sizeof(local);
        getsockname(created->listenFd, reinterpret_cast<struct sockaddr*>(&local), &length);
        created->port = ntohs(local.sin_port);
        {
            std::lock_guard<std::mutex> guard(gLock);
            *newId = gNextForwardId++;
            gForwards[*newId] = created;
        }
        OH_LOG_INFO(LOG_APP, "forward session ready host=%{public}s port=%{public}d",
                    host.c_str(), created->port);
        Forward* entry = created;
        entry->acceptor = std::thread([entry]() {
            while (!entry->stopped.load()) {
                fd_set readSet;
                FD_ZERO(&readSet);
                FD_SET(entry->listenFd, &readSet);
                struct timeval wait;
                wait.tv_sec = 0;
                wait.tv_usec = 20000;
                if (select(entry->listenFd + 1, &readSet, nullptr, nullptr, &wait) <= 0) continue;
                int clientFd = accept(entry->listenFd, nullptr, nullptr);
                if (clientFd < 0) continue;
                std::thread(PumpConnection, entry, clientFd).detach();
            }
        });
    };
    work->build = [created, newId](napi_env e) {
        OH_LOG_INFO(LOG_APP, "forward ready port=%{public}d", created->port);
        napi_value out = nullptr;
        napi_create_object(e, &out);
        napi_value idValue = nullptr;
        napi_create_int32(e, *newId, &idValue);
        napi_set_named_property(e, out, "id", idValue);
        napi_value portValue = nullptr;
        napi_create_int32(e, created->port, &portValue);
        napi_set_named_property(e, out, "port", portValue);
        delete newId;
        return out;
    };
    return StartAsync(env, work);
}

// waygate_ssh.stopForward(id) -> true
static napi_value StopForward(napi_env env, napi_callback_info info) {
    size_t argc = 1;
    napi_value argv[1];
    napi_get_cb_info(env, info, &argc, argv, nullptr, nullptr);
    int32_t id = -1;
    if (argc >= 1) napi_get_value_int32(env, argv[0], &id);
    auto* work = new Work();
    work->run = [id]() {
        Forward* entry = nullptr;
        {
            std::lock_guard<std::mutex> guard(gLock);
            auto found = gForwards.find(id);
            if (found == gForwards.end()) return;
            entry = found->second;
            gForwards.erase(found);
        }
        entry->stopped.store(true);
        if (entry->listenFd >= 0) {
            shutdown(entry->listenFd, SHUT_RDWR);
            close(entry->listenFd);
            entry->listenFd = -1;
        }
        if (entry->acceptor.joinable()) entry->acceptor.join();
        {
            std::lock_guard<std::mutex> guard(entry->lock);
            if (entry->session != nullptr) {
                libssh2_session_disconnect(entry->session, "forward stop");
                libssh2_session_free(entry->session);
                entry->session = nullptr;
            }
            if (entry->sshFd >= 0) {
                close(entry->sshFd);
                entry->sshFd = -1;
            }
        }
        delete entry;
    };
    work->build = [](napi_env e) {
        napi_value out = nullptr;
        napi_get_boolean(e, true, &out);
        return out;
    };
    return StartAsync(env, work);
}

EXTERN_C_START
static napi_value Init(napi_env env, napi_value exports) {
    napi_property_descriptor descriptors[] = {
        {"probeHostKey", nullptr, ProbeHostKey, nullptr, nullptr, nullptr, napi_default, nullptr},
        {"connect", nullptr, Connect, nullptr, nullptr, nullptr, napi_default, nullptr},
        {"exec", nullptr, Exec, nullptr, nullptr, nullptr, napi_default, nullptr},
        {"close", nullptr, Close, nullptr, nullptr, nullptr, napi_default, nullptr},
        {"forward", nullptr, ForwardStart, nullptr, nullptr, nullptr, napi_default, nullptr},
        {"stopForward", nullptr, StopForward, nullptr, nullptr, nullptr, napi_default, nullptr},
    };
    napi_define_properties(env, exports,
                           sizeof(descriptors) / sizeof(descriptors[0]), descriptors);
    return exports;
}
EXTERN_C_END

static napi_module waygateModule = {
    .nm_version = 1,
    .nm_flags = 0,
    .nm_filename = nullptr,
    .nm_register_func = Init,
    .nm_modname = "waygate_ssh",
    .nm_priv = nullptr,
    .reserved = {0},
};

extern "C" __attribute__((constructor)) void RegisterWaygateModule(void) {
    napi_module_register(&waygateModule);
}

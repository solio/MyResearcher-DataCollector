"""HarmonyOS Push Kit v3 / official service account PS256 authentication.

Reference: https://developer.huawei.com/consumer/cn/doc/harmonyos-guides/push-jwt-token
Credentials remain in a private admin file; generated JWTs are never logged.
"""
import json
from pathlib import Path
import time

import jwt

AUDIENCE = "https://oauth-login.cloud.huawei.com/oauth2/v3/token"


def service_token(account, project, now=None):
    if str(account.get("project_id", "")) != project:
        raise ValueError("Push Kit 服务账号与 project_id 不一致")
    if not all(isinstance(account.get(k), str) and account[k] for k in ("key_id", "sub_account", "private_key")):
        raise ValueError("Push Kit 服务账号缺少签名字段")
    now = int(time.time() if now is None else now)
    try:
        return jwt.encode({"aud": AUDIENCE, "iss": account["sub_account"], "iat": now, "exp": now + 3600},
                          account["private_key"].replace("\\n", "\n"), algorithm="PS256",
                          headers={"kid": account["key_id"], "typ": "JWT"})
    except Exception:
        raise ValueError("Push Kit 服务账号私钥无法签名") from None


class TokenProvider:
    def __init__(self):
        self.cached = ""
        self.until = 0
        self.version = None

    def get(self, config):
        path = Path(config["service_account_file"])
        version = (str(path), path.stat().st_mtime_ns, config["project_id"])
        if self.version != version or time.time() >= self.until:
            self.cached = service_token(json.loads(path.read_text()), config["project_id"])
            self.until, self.version = time.time() + 3000, version
        return self.cached

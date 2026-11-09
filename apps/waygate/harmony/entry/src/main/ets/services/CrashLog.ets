import { fileIo as fs } from '@kit.CoreFileKit';

/**
 * Bounded journal for crash evidence.
 *
 * Two files, each capped: `crash.log` for JS-level breadcrumbs (boot / screen / web / life) and
 * `fault.log` for the system FAULT events (jscrash / cppcrash / appfreeze) that arrive through
 * HiAppEvent. When a file reaches its cap the older half is dropped, so neither can grow without
 * bound on a phone with little free space.
 */
class Journal {
  private dir: string = '';
  private name: string = '';
  private maxBytes: number = 48 * 1024;
  private keepBytes: number = 24 * 1024;
  private maxLine: number = 400;
  private lastLine: string = '';

  init(filesDir: string, name: string, maxLine: number): void {
    this.dir = filesDir;
    this.name = name;
    this.maxLine = maxLine;
  }

  path(): string {
    return this.dir ? this.dir + '/' + this.name : '';
  }

  append(tag: string, message: string): void {
    if (!this.dir) {
      return;
    }
    try {
      const now = new Date();
      const pad = (v: number): string => (v < 10 ? '0' + String(v) : String(v));
      const stamp = String(now.getFullYear()) + '-' + pad(now.getMonth() + 1) + '-' + pad(now.getDate()) +
        ' ' + pad(now.getHours()) + ':' + pad(now.getMinutes()) + ':' + pad(now.getSeconds());
      const body = message.replace(/\s+/g, ' ').slice(0, this.maxLine);
      const line = stamp + ' [' + tag + '] ' + body;
      // A crash loop repeats the same line; do not spend space on duplicates.
      const signature = line.slice(20);
      if (signature === this.lastLine) {
        return;
      }
      this.lastLine = signature;
      this.trimIfNeeded();
      const handle = fs.openSync(this.path(), fs.OpenMode.READ_WRITE | fs.OpenMode.CREATE | fs.OpenMode.APPEND);
      try {
        fs.writeSync(handle.fd, line + '\n');
      } finally {
        fs.closeSync(handle);
      }
    } catch (_) {
      // Logging must never take the app down.
    }
  }

  private trimIfNeeded(): void {
    try {
      if (!fs.accessSync(this.path())) {
        return;
      }
      if (fs.statSync(this.path()).size <= this.maxBytes) {
        return;
      }
      const text = fs.readTextSync(this.path());
      const tail = text.length > this.keepBytes ? text.slice(text.length - this.keepBytes) : text;
      const start = tail.indexOf('\n');
      const kept = start >= 0 ? tail.slice(start + 1) : tail;
      const handle = fs.openSync(this.path(), fs.OpenMode.READ_WRITE | fs.OpenMode.CREATE | fs.OpenMode.TRUNC);
      try {
        fs.writeSync(handle.fd, kept);
      } finally {
        fs.closeSync(handle);
      }
    } catch (_) {
      // A full disk must not break the app either.
    }
  }

  tail(lines: number): string {
    if (!this.dir) {
      return '';
    }
    try {
      if (!fs.accessSync(this.path())) {
        return '';
      }
      const all = fs.readTextSync(this.path()).split('\n').filter((line: string) => line.length > 0);
      return all.slice(Math.max(0, all.length - lines)).join('\n');
    } catch (_) {
      return '';
    }
  }

  size(): number {
    try {
      return this.dir && fs.accessSync(this.path()) ? fs.statSync(this.path()).size : 0;
    } catch (_) {
      return 0;
    }
  }
}

export const crashLog = new Journal();
export const faultLog = new Journal();

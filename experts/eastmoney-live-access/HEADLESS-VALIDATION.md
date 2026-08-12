# Fresh-headless validation result

Date: 2026-08-11 (Asia/Shanghai)

## Observations

1. At `17:56:55+08:00`, installed Google Chrome
   `151.0.7922.77` with `--headless=new --dump-dom` and a fresh temporary
   profile returned the real list-page title and HTML for:

   ```text
   https://guba.eastmoney.com/list,601012,f.html
   ```

2. The first version of the reproduction wrapper exposed a Chrome-version
   lifecycle issue: Chrome had produced its document but did not exit by
   itself. The wrapper now detects a complete `</html>` document and then
   terminates only its temporary browser process cleanly.
3. At `18:03:00+08:00`, the corrected wrapper received an Eastmoney
   identity-verification page for the same URL and returned exactly:

   ```json
   {
     "status": "ACCESS_BLOCK",
     "message": "Eastmoney returned an identity-verification page for https://guba.eastmoney.com/list,601012,f.html"
   }
   ```

4. Immediately afterward, the existing browser-managed context still loaded
   the real page title, contained the embedded `var article_list=` marker,
   produced a 303,558-character DOM, and contained no visible verification
   text.

## Conclusion

Fresh headless Chrome is technically viable and did succeed once, but it is
not a reliable replacement for the already proven browser-managed context in
the current source environment. “Non-GUI” improves deployment convenience;
it does not guarantee source acceptance.

The reusable implementation is therefore two-part:

- integration: inject a caller-owned normal browser Page through
  `EastmoneyBrowserTransport`, without claiming this makes the source
  unattended-production-ready;
- diagnosis/server experimentation: use `reproduce_headless.py`, which always
  reports `PASS`, `ACCESS_BLOCK`, schema mismatch, transport failure, or
  repeated pagination explicitly and never attempts a bypass.

This evidence does not prove a private Eastmoney risk rule, an IP ban, or a
fixed request threshold. It only proves the observed response difference.

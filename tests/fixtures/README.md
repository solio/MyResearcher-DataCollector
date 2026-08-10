# Fixture Rules

Future source fixtures must be cropped, deterministic and sanitized versions of observed responses.

Each fixture set records:

- source and observation date;
- source-spec version;
- sanitization performed;
- scenario represented;
- expected parsed output;
- known limitations.

Required scenarios normally include normal, empty, malformed, partial and source-specific abnormal responses.

Never store credentials, authorization headers, cookie values, tokens, full browser profiles or unnecessary personal data. A fixture must preserve behavior without preserving secrets.

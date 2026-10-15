# Waygate cross-environment development

Read repository `AGENTS.md`, this directory's `SPEC.md`, then `runs.md` first.
The user authorized development here and explicitly assigned Windows deployment
and device acceptance to another environment. Continue implementation/fixes as
necessary; do not assume the original conversation or Mac private files exist.

The user explicitly requires automatic commit and push of `runs.md` AND the
source/docs/scripts it refers to after work. Keep handoff status truthful, stage
only relevant reviewed files, preserve unrelated work, and push normally without
force. Credentials, runtime databases, signing configurations/passwords, browser
state and raw authenticated logs must never enter Git. If local signing modifies
the tracked build-profile, restore the public unsigned profile before staging.
Do not report successful device verification from source checks or simulated tests.

Keep human CAPTCHA interaction human. No challenge-solving automation, cookie
exports, implicit source retries, global sibling resume, or training DB writes.
Use the existing desktop helper's ownership and original-request probe guards.
Live source probe/resume is only from the operator's explicit app confirmation.

Windows handoff does not authorize upgrading the existing 36/43 collectors,
changing their tasks, switching their proxy exits, or publishing SSH credentials.
On 2026-10-15 the user initially held deployment, then explicitly assigned the
server side to this agent and requested a startup command for the existing main
console at https://testapi.zuzurent.com.cn/collector (fzz-002). That latest request
authorizes the server integration in deploy/start.sh, including checked nginx
routing, while preserving collector containers/tasks and registry. App changes
and device acceptance remain with the other environment. The Windows agent's
internal systemd hub on 36 is a separate deployment: leave it as-is; it is not
automatically the domain's main console. Do not represent a delivered script as
an executed remote deployment. Keep runs.md current and push referenced changes.

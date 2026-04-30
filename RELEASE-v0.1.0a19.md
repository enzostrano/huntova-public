# Huntova v0.1.0a19 — 2026-04-30

The "kill SaaS leftovers" release. Enzo audit caught the email-
verification banner re-showing in local mode, plus a handful of
"credit"-related copy strings that don't apply to BYOK users. All
gated cleanly behind `hv-saas-only` / `hv-billing-only` /
`hv-auth-only` classes that read `_hvRuntime` at runtime.

## Bug fixes

### Email-verification banner finally gone in local mode
- `verifyBanner` markup gained `hv-saas-only` + `hv-auth-only`
  classes. Either flag false in `_hvRuntime` hides it.
- `app.js` `hvLoadAccount()` was also re-showing the banner because
  the auto-bootstrapped local user object has `email_verified=false`
  by default (no email to verify). Tightened the condition to
  require `_hvRuntime.auth_enabled && !_hvRuntime.single_user_mode`
  before showing.
- `hvCheckTokens()` short-circuits in local / BYOK mode so it
  doesn't try to fetch credit balances that don't apply.

### Credit pill no longer steals click area in local
- `topnav-credits` button gained an explicit `hv-billing-only` class
  so it's `display:none` (not just `visibility:hidden`) in local.

### "Credits run out" copy gated
- `crm-empty-steps` "One credit is used per qualified lead found"
  line wrapped in `hv-billing-only`. Replaced for local users with
  cost-estimate language: "Each qualified lead costs ~$0.04 of API
  spend on your AI provider."
- `start-popup` "until you stop it or credits run out" — the
  "or credits run out" span gated `hv-billing-only`.

## Known issues
- Settings modal "Account / Data" tab still says "Account" in local
  mode (no account in local — lands a20).
- Hunt-launch credit pre-flight gate fires in local mode even though
  credits don't apply (lands a20).
- Providers tab inside Settings doesn't exist yet — users still
  eject to `/setup` to swap keys (lands a20).

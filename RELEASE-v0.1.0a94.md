# Huntova v0.1.0a94 — 2026-05-01

## Bug fixes

### `_all_emails_found` excludes the chosen `contact_email`
- The contact-extraction step picked one address as `contact_email`,
  then dumped the deduplicated full list into `_all_emails_found`.
  When that list contained the chosen address, downstream code that
  iterates *both* fields (drafting / approval queue) could draft a
  second email to the same person, and a side-by-side review on
  small lists looked like the same email twice.
- Now `_all_emails_found` skips the chosen `contact_email`
  (case-insensitive) and the case-insensitive-deduped tail keeps the
  same 8-entry cap.

### Email-history "revert" passes the original-array index
- The previous-versions list renders newest-first
  (`rwH.slice().reverse().forEach(...)`), but the click handler
  passed the *reversed-array* index straight to the
  `/api/leads/.../revert-email` endpoint. The backend reverted
  whatever sat at that index in the *non-reversed* array, so users
  clicking "newest" got the oldest version restored, and vice versa.
- The reversed-loop index is now translated back to the original
  array index before being baked into the onclick.

### Provider-test error message redacts the API key
- `/api/setup/test-provider` (the "Test connection" button) caught
  exceptions and returned `str(e)` truncated to 120 chars in
  `test_message`. Some provider SDKs include the failing key in
  their error messages (e.g. an SDK printing the request URL +
  Authorization header). The full key would then ride back to the
  page in the test response.
- We now replace the literal key (when `len(key) >= 8`) with
  `***redacted***` before formatting the message — defence-in-depth
  on top of the existing keychain storage.

## Updates
- None.

## Known issues
- Same as a93.

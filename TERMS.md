# Terms of use

These terms cover the open-source Huntova CLI and the optional Huntova-hosted surfaces (the marketing site `huntova.com`, the `/try` demo, public share pages, and the cloud SaaS). The CLI itself is governed by the AGPL-3.0-or-later license — these terms are an additional layer for the hosted services and the trademark.

---

## 1. The software

Huntova ("the Software") is licensed under the **GNU Affero General Public License v3.0 or later** ("AGPL-3.0-or-later"). The full license text is in `LICENSE`. By using the Software you accept the AGPL terms. The AGPL grants you wide rights to run, study, share, and modify the Software, with one notable obligation: **if you run a modified version on a network server and let other users interact with it, you must offer those users the source of your modifications**.

You agree not to remove or alter the AGPL license headers in the source files.

## 2. The trademark

"Huntova" and the Huntova logo are trademarks of Enzo Strano. The AGPL license covers the source code; it does not grant trademark rights. You may use the name "Huntova" descriptively (e.g. "I built this with Huntova") and you may distribute unmodified copies under the name. You may not use the Huntova name or logo to identify a forked, modified, or competing distribution without prior written permission.

## 3. Bring your own keys

The Software calls third-party AI providers, SMTP relays, and search backends using credentials *you* supply. **You are solely responsible for those credentials and for any usage they incur**, including:

- Payment for AI tokens consumed during your hunts and rewrites.
- Compliance with your AI provider's acceptable-use policy.
- The deliverability, reputation, and CAN-SPAM / GDPR / CASL / PECR consequences of any email you send through your SMTP relay.

We do not gate, monitor, or rate-limit your provider calls — that's between you and them.

## 4. Acceptable use

You agree not to use Huntova:

- To send unsolicited bulk email in violation of CAN-SPAM, GDPR, CASL, PECR, or any other anti-spam law applicable to you or your recipient.
- To collect personal data on individuals or organisations for purposes prohibited by GDPR, CCPA, or comparable privacy regimes.
- To target individuals on the basis of legally protected characteristics in a way that constitutes unlawful discrimination.
- To research, build, or operate weapons systems, mass-surveillance infrastructure, or covert influence operations.
- To bypass authentication, authorisation, or rate-limiting on any third-party site you don't own or have written permission to access.
- To distribute malware, phishing payloads, or material designed to defraud.

The agent's `BLOCKED_DOMAINS` list, hard-reject heuristics, and AGPL-required source disclosure are belt-and-braces — they don't substitute for your own legal review.

## 5. Cold-email compliance is your responsibility

Most jurisdictions allow B2B cold email under specific conditions (legitimate-interest legal basis, easy unsubscribe, accurate sender, prompt response to opt-out). The Software adds `List-Unsubscribe` and `List-Unsubscribe-Post: List-Unsubscribe=One-Click` headers to outbound mail and supports an unsubscribe webhook plugin, but **drafting compliant copy, honouring opt-outs across hunts, and maintaining a sender-domain reputation are tasks you have to perform**. We provide tools, not legal advice.

## 6. Hosted surfaces

If you sign in to the hosted version at `huntova.com` you additionally agree:

- To not register more than one account per natural person (or per legal entity) without our consent.
- To not abuse free-tier credits via automation, sockpuppet emails, or coordinated multi-account behaviour.
- That billing is processed by Stripe and that chargebacks may result in account suspension while we investigate.
- That `/try` runs in Preview Mode against synthetic data and is not an SLA-bearing surface.

We may suspend or terminate hosted accounts that violate these terms. Refunds for paid plans are issued under our standard policy: pro-rata refund for the unused portion of the current billing period, on request, when the cause of cancellation is service-side.

## 7. Content you generate

You retain ownership of your prompts, your saved leads, your drafted emails, and any other content you produce using Huntova. We claim no ownership over that content.

You grant us a narrow license to process and display *your own* content back to you within the hosted product (so the dashboard can render your leads, the share-page renderer can build your `/h/<slug>` URL, etc.). We do not use your content to train any model, public or proprietary, and we do not share it with any third party except as needed to deliver the service you requested (e.g. forwarding your prompt to your chosen AI provider).

## 8. No warranty

The Software is provided "AS IS" without warranty of any kind, express or implied, including the warranties of merchantability, fitness for a particular purpose, and non-infringement. The AGPL license disclaims warranties; these terms reaffirm that disclaimer for the hosted surfaces.

## 9. Limitation of liability

To the maximum extent permitted by law, in no event will the project authors or contributors be liable for any direct, indirect, incidental, special, exemplary, or consequential damages arising out of or in connection with the use of Huntova, including loss of profits, business interruption, or loss of data, even if advised of the possibility of such damages.

For paid hosted users, our total aggregate liability for any claim arising under these terms is limited to the amount you paid us in the 12 months preceding the claim.

## 10. Updates to these terms

We may update these terms. Material changes are noted in `CHANGELOG.md` with the effective date. Continued use of the hosted surfaces after the effective date constitutes acceptance of the updated terms.

## 11. Governing law

These terms are governed by the laws of the United Kingdom (specifically England and Wales) without regard to conflict-of-law rules. Disputes are resolved in the courts of England and Wales, except that you may bring small-claims-class disputes in your local court of residence where applicable consumer law preserves that right.

## 12. Contact

`enzostrano@gmail.com`.

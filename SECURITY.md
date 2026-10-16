# Security policy

ModelBridge is preview software. Security fixes target the latest preview release; older versions do not have a separate support branch. This project has not received an independent security audit.

## Report a vulnerability

Use [GitHub private vulnerability reporting](https://github.com/leenlbma/modelbridge/security/advisories/new). Do not put exploit credentials or sensitive logs in a public Issue. Include the affected version, reproduction steps using fake data, expected boundary and observed behavior. If private reporting is unavailable, open an Issue asking for a private contact without disclosing exploit details.

## Boundaries and data handling

- The service listens on loopback and validates Host, Origin and authentication. It is not designed to be exposed through a public reverse proxy.
- Upstream keys remain in memory during validation and requests; the gateway stores only a SHA-256 fingerprint. The Claude client separately manages the key entered there. There is no ModelBridge keychain storage yet.
- A separate local administration token controls setup. Electron checks service identity before sending it. The sandboxed renderer cannot run arbitrary commands or receive that token.
- Prompts, tools and relevant project content are sent to the provider selected by the user. ModelBridge cannot guarantee that provider's retention or privacy behavior.
- Exported configuration contains no API Key. The built-in diagnostic export omits keys, fingerprints, prompts, connection names and upstream URLs. A model ID may still be sensitive, so inspect reports before sharing.
- The web console stores its local administration token in session storage. A malicious process running as the same OS user can access local files and credentials; that is outside this isolation boundary.
- Desktop downloads are currently ad-hoc signed, without Developer ID signing or Apple notarization. Verify downloads against the checksums in the same GitHub release. Checksums detect changes; they do not replace publisher signing.

For accidental credential exposure, revoke the credential with its provider and remove it from public artifacts. Deleting one Git commit does not guarantee removal from forks, caches or downloaded files.

# Microsoft Store identity for the Dolmi MSIX: the ONE place to paste the Partner Center values.
# Partner Center > Apps and games > Dolmi > Product management > Product identity shows all three:
#   Package/Identity/Name                 -> IdentityName          (looks like 12345Zehnix.Dolmi)
#   Package/Identity/Publisher            -> Publisher             (looks like CN=1A2B3C4D-....)
#   Package/Properties/PublisherDisplayName -> PublisherDisplayName (your publisher name, e.g. Zehnix)
# Until they are filled, build-msix.ps1 builds a local TEST package with the values under Test*,
# signed with a self-signed certificate. A Store upload needs the real values (and no signing:
# the Store signs it).
@{
  IdentityName         = 'PASTE_PARTNER_CENTER_IDENTITY_NAME'      # e.g. 12345Zehnix.Dolmi
  Publisher            = 'PASTE_PARTNER_CENTER_PUBLISHER_CN'       # e.g. CN=1A2B3C4D-5E6F-7A8B-9C0D-1E2F3A4B5C6D
  PublisherDisplayName = 'PASTE_PARTNER_CENTER_PUBLISHER_DISPLAY_NAME'   # e.g. Zehnix
  DisplayName          = 'Dolmi'                                   # the reserved app name

  # Local test package: its own identity, so it installs next to the classic (Inno) Dolmi.
  TestIdentityName         = 'Zehnix.Dolmi.Test'
  TestPublisher            = 'CN=Dolmi Test Signing'
  TestPublisherDisplayName = 'Zehnix (test)'
  TestDisplayName          = 'Dolmi (Store test)'
}

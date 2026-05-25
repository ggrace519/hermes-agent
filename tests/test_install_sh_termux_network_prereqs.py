"""Regression tests for Termux network prerequisite handling in install.sh."""

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"


# Packages the Termux apt list must include so the install path has working
# build tools + TLS roots + a fetcher. Variable name (`termux_pkgs` vs `pkgs`)
# is incidental — pin the contents instead.
TERMUX_REQUIRED_PKGS = (
    "clang",
    "rust",
    "make",
    "pkg-config",
    "libffi",
    "openssl",
    "ca-certificates",
    "curl",
)


def test_termux_pkg_list_includes_network_basics() -> None:
    text = INSTALL_SH.read_text()
    # Match `local <name>=(pkg1 pkg2 …)` then assert every required package is
    # in that array. Survives renames of the local variable.
    array_match = re.search(
        r"local\s+\w+=\(\s*([^)]*?\bca-certificates\b[^)]*?)\)",
        text,
    )
    assert array_match is not None, (
        "Could not locate the Termux pkg-install array (expected a "
        "`local <name>=(... ca-certificates ...)` line)."
    )
    pkg_blob = array_match.group(1)
    pkg_tokens = pkg_blob.split()
    for required in TERMUX_REQUIRED_PKGS:
        assert required in pkg_tokens, (
            f"Termux pkg list missing {required!r}; found: {pkg_tokens}"
        )


def test_install_script_has_connectivity_probe_and_termux_guidance() -> None:
    text = INSTALL_SH.read_text()
    assert "check_network_prerequisites()" in text
    # The probe hits at least one well-known HTTPS endpoint to validate
    # outbound connectivity. Earlier versions used duckduckgo; the rewrite
    # uses github. Accept either (or any https:// inside the probe function).
    probe_match = re.search(
        r"check_network_prerequisites\(\)\s*\{(?P<body>.*?)^\}",
        text,
        re.DOTALL | re.MULTILINE,
    )
    assert probe_match is not None, "check_network_prerequisites() body not found"
    probe_body = probe_match["body"]
    assert "https://pypi.org/simple/" in probe_body, (
        "Connectivity probe must include pypi.org/simple (pip needs it)."
    )
    assert re.search(r"https://(duckduckgo\.com|github\.com)/", probe_body), (
        "Connectivity probe must reach a second well-known HTTPS endpoint "
        "(duckduckgo.com or github.com) so a pypi outage alone doesn't false-fail."
    )
    # check_network_prerequisites must actually be called from main().
    assert re.search(r"^\s*check_network_prerequisites\s*$", text, re.MULTILINE), (
        "check_network_prerequisites is defined but never called."
    )

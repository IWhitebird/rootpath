#!/bin/sh
# Install the rootpath CLI:
#
#   curl -fsSL https://<this site>/install.sh | sh
#   curl -fsSL https://<this site>/install.sh | sh -s -- --dry-run    # only say what it would do
#
# Downloads the binary for this OS and CPU from the latest GitHub release of IWhitebird/rootpath into
# ~/.local/bin, checks its SHA-256 and that it runs. Without a binary for this platform (or one that runs
# here), it installs uv with Astral's official installer, then rootpath with `uv tool install`: from the
# release's source archive, which has the console built, or else from GitHub, without the console. It
# never uses sudo and never edits your shell's startup files.
#
# Environment:
#   ROOTPATH_INSTALL_DIR   where `rootpath` goes (default ~/.local/bin)
#   ROOTPATH_VERSION       a release tag such as v0.1.0 (default: the latest release)
#   ROOTPATH_DOWNLOAD_URL  where the release files are (default: the GitHub release)
set -eu

REPO="IWhitebird/rootpath"
INSTALL_DIR="${ROOTPATH_INSTALL_DIR:-$HOME/.local/bin}"
VERSION="${ROOTPATH_VERSION:-}"
if [ -n "$VERSION" ]; then
	DOWNLOAD_URL="${ROOTPATH_DOWNLOAD_URL:-https://github.com/$REPO/releases/download/$VERSION}"
	SOURCE="git+https://github.com/$REPO@$VERSION"
else
	DOWNLOAD_URL="${ROOTPATH_DOWNLOAD_URL:-https://github.com/$REPO/releases/latest/download}"
	SOURCE="git+https://github.com/$REPO"
fi
UV_INSTALLER="https://astral.sh/uv/install.sh"
PYTHON=3.12 # the binary's, for `uv tool install`, which can otherwise pick a newer one than rootpath supports
DRY_RUN=0
WORK=""

say() { printf '%s\n' "$*"; }
die() {
	printf 'install.sh: %s\n' "$*" >&2
	exit 1
}
has() { command -v "$1" >/dev/null 2>&1; }

usage() {
	say "usage: install.sh [--dry-run]"
	say "Installs the rootpath CLI into \$ROOTPATH_INSTALL_DIR (default ~/.local/bin). --dry-run only says what it would do."
}

cleanup() { if [ -n "$WORK" ]; then rm -rf "$WORK"; fi; }

# fetch URL FILE
fetch() {
	if has curl; then
		curl -fsSL --retry 2 -o "$2" "$1"
	elif has wget; then
		wget -q -O "$2" "$1"
	else
		die "needs curl or wget"
	fi
}

# exists URL: whether the URL has a file, asking for its first byte only.
exists() {
	if has curl; then
		curl -fsSL -r 0-0 -o /dev/null "$1" 2>/dev/null
	elif has wget; then
		wget -q --spider "$1" 2>/dev/null
	else
		die "needs curl or wget"
	fi
}

# The release files' name for this machine: linux-x86_64, linux-arm64, macos-arm64, macos-x86_64. Other
# systems get a name no release has, so they install with uv.
platform() {
	os=$(uname -s | tr '[:upper:]' '[:lower:]')
	arch=$(uname -m)
	case "$os" in
	darwin) os=macos ;;
	linux) if ldd --version 2>&1 | grep -qi musl; then os=linux-musl; fi ;;
	esac
	case "$arch" in
	x86_64 | amd64) arch=x86_64 ;;
	aarch64 | arm64) arch=arm64 ;;
	esac
	# An x86_64 shell under Rosetta on Apple silicon: the native binary is the one to install.
	if [ "$os-$arch" = macos-x86_64 ] && [ "$(sysctl -n sysctl.proc_translated 2>/dev/null)" = 1 ]; then
		arch=arm64
	fi
	say "$os-$arch"
}

sha256() {
	if has sha256sum; then
		sha256sum "$1" | cut -d ' ' -f 1
	elif has shasum; then
		shasum -a 256 "$1" | cut -d ' ' -f 1
	fi
}

# verify URL FILE: check the download of URL against the SHA-256 the release publishes at URL.sha256, and
# exit, having installed nothing, if they differ.
verify() {
	if ! fetch "$1.sha256" "$2.sha256" 2>/dev/null; then
		say "the release has no checksum file for it: the checksum is not checked"
		return 0
	fi
	want=$(cut -d ' ' -f 1 <"$2.sha256")
	got=$(sha256 "$2")
	if [ -z "$got" ]; then
		say "no sha256sum or shasum here: the checksum is not checked"
	elif [ "$got" != "$want" ]; then
		die "the download does not match its published SHA-256 ($got, expected $want); nothing was installed"
	fi
}

# install_binary URL: download it to $INSTALL_DIR/rootpath. Fails, leaving nothing behind, if the file is
# not the one released or does not run here (`--help`, which older versions without `--version` have too).
install_binary() {
	if [ "$DRY_RUN" = 1 ]; then
		say "would download $1"
		say "  to $INSTALL_DIR/rootpath, check its SHA-256 against $1.sha256 and that it runs"
		return 0
	fi
	say "downloading $1"
	fetch "$1" "$WORK/rootpath" || return 1
	verify "$1" "$WORK/rootpath"
	chmod +x "$WORK/rootpath"
	if ! "$WORK/rootpath" --help >/dev/null 2>&1; then
		say "the $PLATFORM binary does not run on this machine"
		return 1
	fi
	# Copied next to its place, then renamed over it: an old rootpath that is running keeps its file.
	{ mkdir -p "$INSTALL_DIR" && cp "$WORK/rootpath" "$INSTALL_DIR/.rootpath.$$" &&
		mv -f "$INSTALL_DIR/.rootpath.$$" "$INSTALL_DIR/rootpath"; } || die "cannot write to $INSTALL_DIR"
}

install_with_uv() {
	if has uv; then
		uv=$(command -v uv)
	elif [ -x "$INSTALL_DIR/uv" ]; then
		uv="$INSTALL_DIR/uv"
	else
		uv="$INSTALL_DIR/uv"
		if [ "$DRY_RUN" = 1 ]; then
			say "would install uv into $INSTALL_DIR with its official installer ($UV_INSTALLER)"
		else
			say "installing uv into $INSTALL_DIR with its official installer ($UV_INSTALLER)"
			fetch "$UV_INSTALLER" "$WORK/uv-installer.sh"
			UV_INSTALL_DIR="$INSTALL_DIR" UV_NO_MODIFY_PATH=1 sh "$WORK/uv-installer.sh" --quiet
		fi
	fi
	archive="$DOWNLOAD_URL/rootpath-source.tar.gz"
	if exists "$archive"; then
		SOURCE="$archive"
		if [ "$DRY_RUN" = 1 ]; then
			say "would download $archive and check its SHA-256 against $archive.sha256"
		else
			say "downloading $archive"
			fetch "$archive" "$WORK/rootpath-source.tar.gz"
			verify "$archive" "$WORK/rootpath-source.tar.gz"
			SOURCE="$WORK/rootpath-source.tar.gz"
		fi
	else
		say "the release has no source archive ($archive): installing from GitHub, without the console"
		if ! has git; then
			[ "$DRY_RUN" = 1 ] || die "installing rootpath from GitHub needs git: install git and run this again"
			say "(git is needed for that, and is not installed)"
		fi
	fi
	if [ "$DRY_RUN" = 1 ]; then
		say "would run: UV_TOOL_BIN_DIR=$INSTALL_DIR $uv tool install --force --python $PYTHON $SOURCE"
		return 0
	fi
	say "installing rootpath with uv"
	UV_TOOL_BIN_DIR="$INSTALL_DIR" "$uv" tool install --force --quiet --python "$PYTHON" "$SOURCE"
}

next_steps() {
	if [ "$DRY_RUN" = 1 ]; then
		say "dry run: nothing was changed"
		return 0
	fi
	"$INSTALL_DIR/rootpath" --help >/dev/null 2>&1 || die "$INSTALL_DIR/rootpath does not run"
	version=$("$INSTALL_DIR/rootpath" --version 2>/dev/null) || version=rootpath
	say "$version is installed: $INSTALL_DIR/rootpath"
	case ":$PATH:" in
	*":$INSTALL_DIR:"*)
		found=$(command -v rootpath || true)
		if [ "$found" != "$INSTALL_DIR/rootpath" ]; then
			say "note: \`rootpath\` on your PATH is $found, which comes before $INSTALL_DIR"
		fi
		;;
	*)
		say "$INSTALL_DIR is not on your PATH. Add it to your shell's startup file:"
		say "  export PATH=\"$INSTALL_DIR:\$PATH\""
		;;
	esac
	say ""
	say "run: rootpath init"
}

main() {
	for arg in "$@"; do
		case "$arg" in
		--dry-run) DRY_RUN=1 ;;
		-h | --help)
			usage
			exit 0
			;;
		*) die "unknown option $arg (see --help)" ;;
		esac
	done
	if [ "$DRY_RUN" = 0 ]; then
		WORK=$(mktemp -d)
		trap cleanup EXIT
		trap 'exit 1' HUP INT TERM
	fi
	PLATFORM=$(platform)
	asset="$DOWNLOAD_URL/rootpath-$PLATFORM"
	if exists "$asset"; then
		install_binary "$asset" || install_with_uv
	else
		say "no rootpath binary for $PLATFORM in the release ($asset): installing with uv instead"
		install_with_uv
	fi
	next_steps
}

main "$@"

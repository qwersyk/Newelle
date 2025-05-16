{
  stdenv,
  lib,
  python3,
  meson,
  ninja,
  pkg-config,
  wrapGAppsHook4,
  docutils,
  desktopToDarwinBundle,
  vte-gtk4,
  dconf,
  gobject-introspection,
  gsettings-desktop-schemas,
  adwaita-icon-theme,
  gtksourceview5,
  desktop-file-utils,
  lsb-release
}:

let
  pythonDependencies = with python3.pkgs; [
    pygobject3
    libxml2
    requests
    pydub
    gtts
    speechrecognition
    numpy
    matplotlib
    newspaper3k
    lxml
    lxml-html-clean
    pylatexenc
    pyaudio
    tiktoken
    openai
    ollama
    llama-index-core
    llama-index-readers-file
    pip-install-test
  ];
in
stdenv.mkDerivation rec {
  pname = "newelle";
  version = "0.9.6";

  format = "other";

  src = ./.;

  strictDeps = true;

  nativeBuildInputs = [
    meson
    ninja
    gobject-introspection # for setup hook populating GI_TYPELIB_PATH
    docutils
    wrapGAppsHook4
    desktop-file-utils
    pkg-config
  ] ++ lib.optional stdenv.hostPlatform.isDarwin desktopToDarwinBundle;

  buildInputs =
    [
      python3
      vte-gtk4
      dconf
      adwaita-icon-theme
      gsettings-desktop-schemas
      gtksourceview5
      desktop-file-utils
      lsb-release
    ];

    preFixup = ''
     glib-compile-schemas $out/share/gsettings-schemas/${pname}-${version}/glib-2.0/schemas
     gappsWrapperArgs+=(--set PYTHONPATH "${python3.pkgs.makePythonPath pythonDependencies}")
     patchShebangs $out/bin
   '';

  meta = with lib; {
    homepage = "https://github.com/qwersyk/Newelle";
    description = "Newelle - Your Ultimate Virtual Assistant ";
    mainProgram = "newelle";
    license = licenses.gpl3;
    platforms = platforms.unix;
  };

}

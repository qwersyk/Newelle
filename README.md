<h1 align="center">
  <img src="https://raw.githubusercontent.com/qwersyk/Newelle/master/data/icons/hicolor/scalable/apps/io.github.qwersyk.Newelle.svg" alt="Newelle" width="192" height="192"/>
  <br>
  Newelle - Your Ultimate Virtual Assistant
</h1>
<p align="center">
  <a href="https://flathub.org/apps/details/io.github.qwersyk.Newelle">
      <picture>
        <source srcset="https://dl.flathub.org/assets/badges/flathub-badge-i-en.svg" media="(prefers-color-scheme: light)">
        <source srcset="https://raw.githubusercontent.com/qwersyk/Assets/main/flathub-badge-dark.svg" media="(prefers-color-scheme: dark)">
        <img width="200" alt="Download on Flathub" src="https://dl.flathub.org/assets/badges/flathub-badge-i-en.svg"/>
      </picture>
    </a>
    <a href="https://github.com/topics/newelle-extension">
      <picture>
        <source srcset="https://raw.githubusercontent.com/qwersyk/Assets/main/newelle-extension.svg" media="(prefers-color-scheme: light)">
        <source srcset="https://raw.githubusercontent.com/qwersyk/Assets/main/newelle-extension-dark.svg" media="(prefers-color-scheme: dark)">
        <img width="200" alt="Download on Flathub" src="https://raw.githubusercontent.com/qwersyk/Assets/main/newelle-extension.svg"/>
      </picture>
    </a>
    <a href="https://github.com/qwersyk/Newelle/wiki">
      <picture>
        <source srcset="https://raw.githubusercontent.com/qwersyk/Assets/main/newelle-wiki.svg" media="(prefers-color-scheme: light)">
        <source srcset="https://raw.githubusercontent.com/qwersyk/Assets/main/newelle-wiki-dark.svg" media="(prefers-color-scheme: dark)">
        <img width="200" alt="Wiki for Newelle" src="https://raw.githubusercontent.com/qwersyk/Assets/main/newelle-wiki.svg"/>
      </picture>
    </a>
    <br>
</p>
<picture>
  <source srcset="https://raw.githubusercontent.com/qwersyk/Newelle/master/screenshots/1w.png" media="(prefers-color-scheme: light)">
  <source srcset="https://raw.githubusercontent.com/qwersyk/Newelle/master/screenshots/1b.png" media="(prefers-color-scheme: dark)">
  <img src="https://raw.githubusercontent.com/qwersyk/Newelle/master/screenshots/1w.png" alt="screenshot">
</picture>

# Features

- 🎨 **Advanced Customization**: Tailor the application with a wide range of settings
- 🚀 **Flexible Model Support**: Choose from mutliple AI models and providers to fit your specific needs
- 💻 **Terminal Command Exection**: Execute commands suggested by the AI on the fly
- 🧩 **Extensions**: Add your own functionalities and models to Newelle
- 🗣 **Voice support**: Chat hands free with Newelle, supporting many Speech To Text and TTS models
- 🧠 **Long Term Memory**: Remember conversations from previous chats
- 💼 **Chat with documents**: Chat with your own documents
- 🔎 **Web Search**: Provide reliable answers using Web Search
- 🌐 **Website Reading**: Ask any information about any website by writing #https://.. question
- 👤 **Profile Manager**: Create settings profiles and switch between them on the go
- 📁 **Builtin File Manager**: Manage your files with the help of AI
- 📝 **Rich Formatting**: Support for Markdown and LaTeX
- ✏️ **Chat editing**: Edit or remove any message and manage your prompts easily

<picture>
  <source srcset="https://raw.githubusercontent.com/qwersyk/Newelle/master/screenshots/3w.png" media="(prefers-color-scheme: light)">
  <source srcset="https://raw.githubusercontent.com/qwersyk/Newelle/master/screenshots/3b.png" media="(prefers-color-scheme: dark)">
  <img src="https://raw.githubusercontent.com/qwersyk/Newelle/master/screenshots/3w.png" alt="screenshot">
</picture>

# Extensions

Newelle supports extensions to enhance its functionality. You can either
use [existing extensions](https://github.com/topics/newelle-extension) or create your own to add new features to the
application.

<picture>
  <source srcset="https://raw.githubusercontent.com/qwersyk/Newelle/master/screenshots/2w.png" media="(prefers-color-scheme: light)">
  <source srcset="https://raw.githubusercontent.com/qwersyk/Newelle/master/screenshots/2b.png" media="(prefers-color-scheme: dark)">
  <img src="https://raw.githubusercontent.com/qwersyk/Newelle/master/screenshots/2w.png" alt="screenshot">
</picture>

## Mini Window Mode

A lightweight version of Newelle that can be triggered via keyboard shortcuts.

### Configuration

#### 1. Set Global Keyboard Shortcut
To configure the mini window launch (example using Ctrl+Space), set the following command in your system keyboard settings:

```bash
/bin/bash -c 'flatpak run --command=gsettings io.github.qwersyk.Newelle set io.github.qwersyk.Newelle startup-mode "mini" && flatpak run io.github.qwersyk.Newelle'
```

#### 2. Enable Window Centering
For GNOME desktop environment users, you may need to enable automatic window centering:

```bash
gsettings set org.gnome.mutter center-new-windows true
```

<picture>
  <source srcset="https://raw.githubusercontent.com/qwersyk/Newelle/master/screenshots/4w.png" media="(prefers-color-scheme: light)">
  <source srcset="https://raw.githubusercontent.com/qwersyk/Newelle/master/screenshots/4b.png" media="(prefers-color-scheme: dark)">
  <img src="https://raw.githubusercontent.com/qwersyk/Newelle/master/screenshots/4w.png" alt="screenshot">
</picture>

# Installation

<a href="https://github.com/qwersyk/Newelle/archive/refs/heads/master.zip">
  <picture>
    <source srcset="https://raw.githubusercontent.com/qwersyk/Assets/main/builder.svg" media="(prefers-color-scheme: light)">
    <source srcset="https://raw.githubusercontent.com/qwersyk/Assets/main/builder-dark.svg" media="(prefers-color-scheme: dark)">
    <img src="https://raw.githubusercontent.com/qwersyk/Assets/main/builder.svg" alt="builder">
  </picture>
</a>
There are two ways of doing this

  * `install.sh`
    1. Install the latest Gnome SDK
    2. Run `sh install.sh`
    3. Profit!
  * Gnome Builder
    1. Install GNOME Builder on your system.
    2. Clone the Newelle repository from GitHub.
    3. Open the project in GNOME Builder and compile it.
    4. Once compiled, you can run the program from the compiled executable.

<a href="https://nixos.org">
  <picture>
    <source srcset="https://raw.githubusercontent.com/qwersyk/Assets/main/nix.svg" media="(prefers-color-scheme: light)">
    <source srcset="https://raw.githubusercontent.com/qwersyk/Assets/main/nix-dark.svg" media="(prefers-color-scheme: dark)">
    <img src="https://raw.githubusercontent.com/qwersyk/Assets/main/nix.svg" alt="builder">
  </picture>
</a>

Run without installing:

1. run `nix run github:qwersyk/Newelle`

For development:

2. Clone the Newelle repository from GitHub.
3. In terminal change dir to repository
4. run `nix run .` to start the program or run `nix develop .` to start a developer shell;

<a href="https://github.com/qwersyk/Newelle/actions">
  <picture>
    <source srcset="https://raw.githubusercontent.com/qwersyk/Assets/main/beta.svg" media="(prefers-color-scheme: light)">
    <source srcset="https://raw.githubusercontent.com/qwersyk/Assets/main/beta-dark.svg" media="(prefers-color-scheme: dark)">
    <img src="https://raw.githubusercontent.com/qwersyk/Assets/main/beta.svg" alt="beta">
  </picture>
</a>

> [!WARNING]
> Localizations will not work on these builds! If you want your language to work, go to the
> "Builder" section instead
1. Download the latest release from the [Github Actions](https://github.com/qwersyk/Newelle/actions)
2. Extract the downloaded package.
3. Install a flatpak package.

<a href="https://flathub.org/apps/io.github.qwersyk.Newelle">
  <picture>
    <source srcset="https://raw.githubusercontent.com/qwersyk/Assets/main/flathub.svg" media="(prefers-color-scheme: light)">
    <source srcset="https://raw.githubusercontent.com/qwersyk/Assets/main/flathub-dark.svg" media="(prefers-color-scheme: dark)">
    <img src="https://raw.githubusercontent.com/qwersyk/Assets/main/flathub.svg" alt="flathub">
  </picture>
</a>

1. Ensure you have Flatpak installed on your system.
2. Install Newelle by executing: `flatpak install flathub io.github.qwersyk.Newelle`

# Permission

> [!IMPORTANT]
> The Flathub version of Newelle is restricted to the `.var/app/io.github.qwersyk.Newelle` folder and operates within a
> Flatpak virtualized environment, limiting its capabilities.

To extend functionality, you can either temporarily grant access with:
```flatpak run --talk-name=org.freedesktop.Flatpak --filesystem=home io.github.qwersyk.Newelle```
or adjust settings permanently using Flatseal:

- Open Flatseal, find "Newelle," enable "All user files" and "Session Bus," and add `org.freedesktop.Flatpak` to run
  outside the sandbox.

> [!WARNING]
> Be cautious when enabling these options. They reduce security by exposing your data and terminal. Avoid sharing
> personal information, and understand that we can't guarantee the privacy of your chat data or prevent potential risks
> from proprietary models.

# Alternative Versions

<a href="https://github.com/qwersyk/Newelle/tree/aarch64">
  <picture>
    <source srcset="https://raw.githubusercontent.com/qwersyk/Assets/main/aarch64.svg" media="(prefers-color-scheme: light)">
    <source srcset="https://raw.githubusercontent.com/qwersyk/Assets/main/aarch64-dark.svg" media="(prefers-color-scheme: dark)">
    <img src="https://raw.githubusercontent.com/qwersyk/Assets/main/aarch64.svg" alt="aarch64">
  </picture>
</a>


**[Newelle Lite](https://github.com/qwersyk/Newelle/tree/aarch64)** - Your Virtual Assistant for aarch64

<a href="https://github.com/NyarchLinux/NyarchAssistant">
  <picture>
    <source srcset="https://raw.githubusercontent.com/qwersyk/Assets/main/nyarch.svg" media="(prefers-color-scheme: light)">
    <source srcset="https://raw.githubusercontent.com/qwersyk/Assets/main/nyarch-dark.svg" media="(prefers-color-scheme: dark)">
    <img src="https://raw.githubusercontent.com/qwersyk/Assets/main/nyarch.svg" alt="nyarch">
  </picture>
</a>

**[Nyarch Assistant](https://github.com/NyarchLinux/NyarchAssistant)** - Your ultimate Waifu AI Assistant

<picture>
  <source srcset="https://raw.githubusercontent.com/NyarchLinux/NyarchAssistant/refs/heads/master/screenshots/1w.png" media="(prefers-color-scheme: light)">
  <source srcset="https://raw.githubusercontent.com/NyarchLinux/NyarchAssistant/refs/heads/master/screenshots/1b.png" media="(prefers-color-scheme: dark)">
  <img src="https://raw.githubusercontent.com/NyarchLinux/NyarchAssistant/refs/heads/master/screenshots/1w.png" alt="screenshot">
</picture>

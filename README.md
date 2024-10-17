<h1 align="center">
  <img src="https://raw.githubusercontent.com/NyarchLinux/NyarchAssistant/refs/heads/master/data/icons/hicolor/scalable/apps/moe.nyarchlinux.assistant.svg" alt="nyarchassistant" width="192" height="192"/>
  <br>
  Nyarch Assistant - Your ultimate Waifu AI Assistant
</h1>
<p align="center">
  <a href="https://github.com/topics/newelle-extension">
    <img width="200" alt="Newelle Extensions" src="https://raw.githubusercontent.com/qwersyk/Assets/main/newelle-extension.svg"/>
  </a>
  <a href="https://github.com/qwersyk/Newelle/wiki">
    <img width="200" alt="Wiki for Nyarch Assistant" src="https://raw.githubusercontent.com/qwersyk/Assets/main/newelle-wiki.svg"/>
  </a>
  <br>
</p>

![screenshot](https://raw.githubusercontent.com/NyarchLinux/NyarchAssistant/refs/heads/master/screenshots/1w.png#gh-light-mode-only)
![screenshot](https://raw.githubusercontent.com/NyarchLinux/NyarchAssistant/refs/heads/master/screenshots/1b.png#gh-dark-mode-only)

# Features
- **Your dream waifu, at your command**: Choose from a vast library of TTS voices and Live2D or LivePNG models to create the perfect virtual companion.

- **Terminal Command Execution**: Execute terminal commands directly through the AI.

- **Advanced Customization**: Tailor the application with a wide range of settings.

- **Flexible Model Support**: Choose from multiple AI models to fit your specific needs.

![screenshot](https://raw.githubusercontent.com/NyarchLinux/NyarchAssistant/refs/heads/master/screenshots/3w.png#gh-light-mode-only)
![screenshot](https://raw.githubusercontent.com/NyarchLinux/NyarchAssistant/refs/heads/master/screenshots/3b.png#gh-dark-mode-only)

# Extensions

nyarchassistant supports extensions to enhance its functionality. You can either use [existing extensions](https://github.com/topics/nyarchassistant-extension) or create your own to add new features to the application.

![screenshot](https://raw.githubusercontent.com/qwersyk/newelle/master/screenshots/2w.png#gh-light-mode-only)
![screenshot](https://raw.githubusercontent.com/qwersyk/newelle/master/screenshots/2b.png#gh-dark-mode-only)

# Installation

![builder](https://raw.githubusercontent.com/qwersyk/Assets/main/builder.svg#gh-light-mode-only)
![builder](https://raw.githubusercontent.com/qwersyk/Assets/main/builder-dark.svg#gh-dark-mode-only)

1. Install GNOME Builder on your system.
2. Clone the nyarchassistant repository from GitHub.
3. Open the project in GNOME Builder and compile it.
4. Once compiled, you can run the program from the compiled executable.

![beta](https://raw.githubusercontent.com/qwersyk/Assets/main/beta.svg#gh-light-mode-only)
![beta](https://raw.githubusercontent.com/qwersyk/Assets/main/beta-dark.svg#gh-dark-mode-only)

1. Download the latest release from the [Github Actions](https://github.com/qwersyk/nyarchassistant/actions)
2. Extract the downloaded package.
3. Install a flatpak package.

# Permission

To extend functionality, you can either temporarily grant access with:
```flatpak run --talk-name=org.freedesktop.Flatpak --filesystem=home moe.nyarchlinux.nyarchassistant```
or adjust settings permanently using Flatseal:
- Open Flatseal, find "nyarchassistant," enable "All user files" and "Session Bus," and add `org.freedesktop.Flatpak` to run outside the sandbox.

> [!WARNING]
> Be cautious when enabling these options. They reduce security by exposing your data and terminal. Avoid sharing personal information, and understand that we can't guarantee the privacy of your chat data or prevent potential risks from proprietary models.

<h1 align="center">
  <img src="https://raw.githubusercontent.com/qwersyk/nyarchassistant/master/data/icons/hicolor/scalable/apps/io.github.qwersyk.nyarchassistant.svg" alt="nyarchassistant" width="192" height="192"/>
  <br>
  nyarchassistant - Your Ultimate Virtual Assistant
</h1>
<p align="center">
  <a href="https://flathub.org/apps/details/io.github.qwersyk.nyarchassistant">
    <img width="200" alt="Download on Flathub" src="https://dl.flathub.org/assets/badges/flathub-badge-i-en.svg"/>
  </a>
  <a href="https://github.com/topics/nyarchassistant-extension">
    <img width="200" alt="Download on Flathub" src="https://raw.githubusercontent.com/qwersyk/Assets/main/nyarchassistant-extension.svg"/>
  </a>
  <a href="https://github.com/qwersyk/nyarchassistant/wiki">
    <img width="200" alt="Wiki for nyarchassistant" src="https://raw.githubusercontent.com/qwersyk/Assets/main/nyarchassistant-wiki.svg"/>
  </a>
  <br>
</p>
<p align="center">
<a href="https://stopthemingmy.app">
    <img width="180" alt="Please Don't Theme" src="https://stopthemingmy.app/badge.svg"/>
  </a>
  <br>
</p>

![screenshot](https://raw.githubusercontent.com/qwersyk/nyarchassistant/master/screenshots/1w.png#gh-light-mode-only)
![screenshot](https://raw.githubusercontent.com/qwersyk/nyarchassistant/master/screenshots/1b.png#gh-dark-mode-only)

# Features

- **Terminal Command Execution**: Execute terminal commands directly through the AI.

- **Advanced Customization**: Tailor the application with a wide range of settings.

- **Flexible Model Support**: Choose from multiple AI models to fit your specific needs.

![screenshot](https://raw.githubusercontent.com/qwersyk/nyarchassistant/master/screenshots/3w.png#gh-light-mode-only)
![screenshot](https://raw.githubusercontent.com/qwersyk/nyarchassistant/master/screenshots/3b.png#gh-dark-mode-only)

# Extensions

nyarchassistant supports extensions to enhance its functionality. You can either use [existing extensions](https://github.com/topics/nyarchassistant-extension) or create your own to add new features to the application.

![screenshot](https://raw.githubusercontent.com/qwersyk/nyarchassistant/master/screenshots/2w.png#gh-light-mode-only)
![screenshot](https://raw.githubusercontent.com/qwersyk/nyarchassistant/master/screenshots/2b.png#gh-dark-mode-only)

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

![flathub](https://raw.githubusercontent.com/qwersyk/Assets/main/flathub.svg#gh-light-mode-only)
![flathub](https://raw.githubusercontent.com/qwersyk/Assets/main/flathub-dark.svg#gh-dark-mode-only)

1. Ensure you have Flatpak installed on your system.
2. Install nyarchassistant by executing: `flatpak install flathub io.github.qwersyk.nyarchassistant`

# Permission

> [!IMPORTANT]
> The Flathub version of nyarchassistant is restricted to the `.var/app/io.github.qwersyk.nyarchassistant` folder and operates within a Flatpak virtualized environment, limiting its capabilities.

To extend functionality, you can either temporarily grant access with:
```flatpak run --talk-name=org.freedesktop.Flatpak --filesystem=home io.github.qwersyk.nyarchassistant```
or adjust settings permanently using Flatseal:
- Open Flatseal, find "nyarchassistant," enable "All user files" and "Session Bus," and add `org.freedesktop.Flatpak` to run outside the sandbox.

> [!WARNING]
> Be cautious when enabling these options. They reduce security by exposing your data and terminal. Avoid sharing personal information, and understand that we can't guarantee the privacy of your chat data or prevent potential risks from proprietary models.

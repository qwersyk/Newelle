<h1 align="center">
  <img src="https://raw.githubusercontent.com/qwersyk/Newelle/master/data/icons/hicolor/scalable/apps/io.github.qwersyk.Newelle.svg" alt="Newelle" width="192" height="192"/>
  <br>
  Newelle - Your Ultimate Virtual Assistant
</h1>
<p align="center">
  <a href="https://flathub.org/apps/details/io.github.qwersyk.Newelle">
    <img width="200" alt="Download on Flathub" src="https://dl.flathub.org/assets/badges/flathub-badge-i-en.svg"/>
  </a>
  <a href="https://github.com/topics/newelle-extension">
    <img width="200" alt="Download on Flathub" src="https://raw.githubusercontent.com/qwersyk/Assets/5f06b2019c72ba7faf2d3bfe1b6192cbcc69a0d7/newelle-extension.svg"/>
  </a>
  <br>
</p>
<p align="center">
<a href="https://stopthemingmy.app">
    <img width="180" alt="Please Don't Theme" src="https://stopthemingmy.app/badge.svg"/>
  </a>
  <br>
</p>

![screenshot](https://raw.githubusercontent.com/qwersyk/Newelle/master/data/screenshots/screenshot1.png)

**Newelle** is an advanced chat bot that aims to revolutionize your virtual assistant experience. Here's a concise overview of its key features:

## Features

- **File and System Management:** Take control of your computer effortlessly. Newelle enables you to create folders, rename files, edit content, and access detailed file information, enhancing your overall productivity.

- **Code Execution and Collaboration:** Execute Python code seamlessly within the chat interface. Collaborate on coding projects, seek assistance, and share code snippets effortlessly with Newelle.

- **Intuitive Graphical Interface:** Enjoy a visually appealing interface with a sidebar for chat history and a file explorer panel. Drag and drop files and folders directly into the chat, streamlining your workflow.

- **Flexible Message Manipulation:** Edit, continue, or regenerate messages easily. Right-click on a user's message to make quick modifications. Newelle grants you full control over your conversations.

- **Effortless Chat Management:** Copy and save chat conversations effortlessly for future reference. Newelle automatically generates names for chat sessions, making organization a breeze.

# Installation and Getting Started

To start using our program, you have two options: compiling it through GNOME Builder or downloading the release from GitHub. Additionally, we have provided a limited version of the program on Flathub.

## Compiling with GNOME Builder

1. Install GNOME Builder on your system.
2. Clone the Newelle repository from GitHub.
3. Open the project in GNOME Builder and compile it.
4. Once compiled, you can run the program from the compiled executable.

## Downloading from GitHub

1. Visit the Newelle GitHub repository.
2. Navigate to the "Releases" section.
3. Download the latest release package compatible with your operating system.
4. Extract the downloaded package.
5. Run the program from the extracted files.

## Installing from Flathub

1. Ensure you have Flatpak installed on your system.
2. Install Newelle by executing: `flatpak install flathub io.github.qwersyk.Newelle`
3. Once installed, you can launch Newelle.

Please note that the Flatpak version of Newelle has some limitations for security purposes. It can only access the `.var/app/ioюgithub.qwersyk.Newelle` folder, and it can only run within the Flatpak sandboxed environment. 

To extend the program's capabilities, follow these steps:

1. Install Flatseal on your system.
2. Launch Flatseal and locate "Newelle" in the application list.
3. Enable the "All user files" permission for Newelle to access user files.
4. To allow Newelle to run outside the Flatpak sandbox, enable the "Session Bus" permission and add a new talk with the name "org.freedesktop.Flatpak".
5. Disable virtualization in the program settings to run Newelle outside the Flatpak sandbox.

Please note that by performing these steps, the program's security may be compromised as it gains access to your data and terminal. Although our program is open-source and can be verified for malicious actions, the underlying "baichat" model is proprietary. We cannot guarantee where your chat data is sent or rule out the possibility of incorrect or malicious commands from the neural network. Please be careful when enabling these options.


> By running the following command when launching the program, you can grant temporary access to memory and the console:```flatpak run --talk-name=org.freedesktop.Flatpak --filesystem=home io.github.qwersyk.Newelle```

> Our bot operates thanks to the [BAI chat](https://chatbot.theb.ai/) and [baichat-py](https://github.com/Bavarder/baichat-py)( developed by [Bavarder](https://bavarder.codeberg.page/)). We would like to express our gratitude to them for their invaluable contributions.

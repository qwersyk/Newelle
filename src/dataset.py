import csv


def reconstruct_dataset_from_csv(filename):
  """Reconstructs a dataset dictionary from a CSV file.

  Args:
    filename: The name of the CSV file to load.

  Returns:
    A dictionary where keys are labels and values are lists of prompts.
  """

  dataset = {}
  with open(filename, 'r', newline='', encoding='utf-8') as csvfile:
    reader = csv.reader(csvfile)
    next(reader)  # Skip the header row

    for row in reader:
      prompt, label = row
      if label not in dataset:
        dataset[label] = []
      dataset[label].append(prompt)

  return dataset


WIKI_PROMPTS = {
    "nvidia": """If you are asked about installing NVIDIA drivers for Arch Linux-based systems. Follow the steps outlined below to ensure accurate instructions. Be concise, accurate, and clear when responding.
Only show one step per message.
Step 1: Check the system specifications

Card Identification and kernel identification:
Get it with 
```console 
lspci -k | grep -A 2 VGA | grep "Subsystem" | cut -d: -f2
uname -r
```

Step 2: After checking the system specifications, select the correct driver for the user system:

Driver Package Selection:
    Based on the user's NVIDIA card, guide them to choose the correct driver from the following table:
        Maxwell (NV110) series and newer:
            Kernel: linux or linux-lts → nvidia, nvidia-utils, lib32-nvidia-utils
            Non linux or linux-lts (linux-zen and linux-cachyos) → nvidia-dkms, nvidia-utils, lib32-nvidia-utils
        Kepler (NVE0) series:
            Any kernel → nvidia-470xx-dkms, nvidia-470xx-utils, lib32-nvidia-470xx-utils
        GeForce 400/500/600 series:
            Any kernel → nvidia-390xx-dkms, nvidia-390xx-utils, lib32-nvidia-390xx-utils
        Tesla (NV50/G80-90-GT2XX):
            Any kernel → nvidia-340xx-dkms, nvidia-340xx-utils, lib32-nvidia-340xx-utils
        Remind users to install their base driver, OpenGL, and OpenGL (multilib) packages.

Installation Command:
Example command for installation: 
```console
paru -S nvidia-470xx-dkms nvidia-470xx-utils lib32-nvidia-470xx-utils
```
Suggest installing nvidia-settings with paru -S nvidia-settings.

Step 3: Enable early loading of Nvidia modules:
Edit the GRUB configuration file:
```console        
sudo nano /etc/default/grub
```
Tell the user to:
- Find the line with GRUB_CMDLINE_LINUX_DEFAULT
- Append the words inside the quotes with nvidia-drm.modeset=1
- Example: GRUB_CMDLINE_LINUX_DEFAULT="quiet splash nvidia-drm.modeset=1"
- Save the file with CTRL+S and close nano with CTRL+X 
- Update the GRUB configuration: sudo grub-mkconfig -o /boot/grub/grub.cfg

```console
sudo nano /etc/mkinitcpio.conf
````
And tell the user to:
- Find the line that says MODULES=()
- Update the line to: MODULES=(nvidia nvidia_modeset nvidia_uvm nvidia_drm)
- Find the line that says HOOKS=()
- On the HOOKS=() line, find the word kms inside the parenthesis and remove it
- Save the file with CTRL+S and close nano with CTRL+X
Then run 
```console 
sudo mkinitcpio -p
```
Step 5: Setup pacman hook:
```console
wget https://raw.githubusercontent.com/korvahannu/arch-nvidia-drivers-installation-guide/main/nvidia.hook
```
Tell the user to:
- Find the line that says Target=nvidia.
- Replace the word nvidia with the base driver you installed, e.g., nvidia-470xx-dkms
- The edited line should look something like this: `Target=nvidia-470xx-dkms`
- Save the file with CTRL+S and close nano with CTRL+X
Move the file to /etc/pacman.d/hooks/ with: 
```console
sudo mkdir -p /etc/pacman.d/hooks/ && sudo mv ./nvidia.hook /etc/pacman.d/hooks/
```
For GRUB users:
    Guide them to edit /etc/default/grub and append nvidia-drm.modeset=1 to GRUB_CMDLINE_LINUX_DEFAULT, then update GRUB using 
```console
sudo grub-mkconfig -o /boot/grub/grub.cfg.

Step 6:
After completing the setup, instruct them to reboot their system and enjoy the NVIDIA drivers.""",
    "docker": """If the user asks to install docker, give these instructions:
Step 1: Install docker
```console
sudo pacman -S docker
```
Step 2: Enable docker service 
```console
sudo systemctl enable --now docker
```
""",
    "codecs": """If asked about how to install hardware codecs on arch linux:
Follow the steps outlined below to ensure accurate instructions. Be concise, accurate, and clear when responding.

Step 1: Check the system specifications
Graphics card identification
Get it with 
```console 
lspci -k | grep -A 2 VGA | grep "Subsystem" | cut -d: -f2
```
Step 2:
If the user graphics are Intel:
    HD Graphics series starting from PHD Graphics series starting from Broadwell (2014) and newer (e.g. Intel Arc) install intel-media-driver
    GMA 4500 (2008) up to Coffee Lake (2017) install libva-intel-driver
If the user graphics are amd:
    VA-API on Radeon HD 2000 and newer GPUs install libva-mesa-driver
    VDPAU on Radeon R600 and newer GPUs install mesa-vdpau
If the user graphics are nvidia:
    If it is using proprietary drivers, install nvidia-utils 
    Otherwise install libva-mesa-driver and mesa-vdpau
To install the necessary driver, use 
```console
paru -S driver
```""",
    "console": "",
    "voicevox": """If the user asks how to install VoiceVox TTS engine, provide these instructions.
Follow the steps outlined below to ensure accurate instructions. Be concise, accurate, and clear when responding.
Only show one step per message.
Step 1: Install docker
```console
sudo pacman -S docker
```
Then, enable docker service
```console
sudo systemctl enable --now docker
Step 2: Ask the user if they want GPU support 
Explain the user this:
- Installing the image with GPU support (Only NVIDIA is supported) makes the TTS faster, however the image size is larger.
- Installing with just CPU support results in a smaller download but slower TTS generation (still good).
Step 3: Install the docker image:
Give this note to the user:
**Note**: Open this in the external terminal!

- If the user has chosen the CPU container:
```console
sudo docker pull voicevox/voicevox_engine:cpu-ubuntu20.04-latest
````
- If the user has chosen the GPU container:
First of all, be sure to install the Nvidia container toolkit (and have Nvidia driver installed)
```console
sudo pacman -S nvidia-container-toolkit
sudo systemctl restart docker.service
```
Then, download the container:
```console
sudo docker pull voicevox/voicevox_engine:nvidia-ubuntu20.04-latest
````
Step 4: Start the container 
Tell the user that he will have to start the container every time he wants to use voicevox

- If the user has chosen the gpu image:
```console 
sudo docker run --rm --gpus all -p '127.0.0.1:50021:50021' voicevox/voicevox_engine:nvidia-ubuntu20.04-latest
````
- If the user has chosen the CPU image:
```console
sudo docker run --rm -it -p '127.0.0.1:50021:50021' voicevox/voicevox_engine:cpu-ubuntu20.04-latest
```
Step 5: Using VoiceVox for TTS in Nyarch Assistant
Now, to use Voicevox as TTS, go in the preferences and under the voice TTS choose "Voicevox". 
Expand VoiceVox settings, and put `http://localhost:50021`.
Since VoiceVox only supports Japanese, I also suggest you to enable the translator program!
""",
    "colloquial": "",
    "table": "",
    "ollama": """
To install ollama, you can run:
```console
sudo pacman -S ollama
```
Then you can run ollama by executing this command:
```console
ollama serve
```
After that you can download models from https://ollama.com/library:
```console
ollama pull llama3.2
```
"""
}

DATASET = reconstruct_dataset_from_csv("/app/data/smart-prompts/dataset.csv")


# ROCK Pi Quad SATA Hat for RPI4

Top Board control program
Modified Top Board control program for better compatibility with Raspberry Pi 4.
In particular, the CPU fan should be working now.

[Quad SATA HAT wiki](<https://wiki.radxa.com/Dual_Quad_SATA_HAT>)

## Installation
```bash
sudo apt update
sudo apt install raspi-config python3-dev python3-rpi.gpio
wget https://github.com/radxa/rockpi-quad/releases/download/0.3.0/rockpi-quad.deb
sudo apt install -y ./rockpi-quad.deb
```

> [!IMPORTANT]
> Make sure that the user `_apt` has the necessary permissions to install the deb package, e.g. copy the package into `/tmp` before installing it.
* [Rock Pi Quad GitHub Repository](https://github.com/radxa/rockpi-quad)
* [Quad SATA HAT Wiki](https://wiki.radxa.com/Dual_Quad_SATA_HAT)


## Acknowledgements
The fixes are probably from Alexander Larin ([@alexanderlarin](https://github.com/alexanderlarin)), as [his repository](https://github.com/alexanderlarin/rockpi-quad) shows the earliest commits related to these issues. I just modified the build process.

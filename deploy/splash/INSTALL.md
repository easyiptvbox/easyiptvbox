# EasyIPTVBox boot / shutdown splash (Plymouth)

    sudo apt install -y plymouth plymouth-themes
    python3 make_splash.py easyiptvbox          # (re)generate the PNGs
    sudo mkdir -p /usr/share/plymouth/themes/easyiptvbox
    sudo cp easyiptvbox/* /usr/share/plymouth/themes/easyiptvbox/
    sudo /usr/sbin/plymouth-set-default-theme easyiptvbox
    printf "[Daemon]\nTheme=easyiptvbox\nShowDelay=0\nDeviceTimeout=8\n" | sudo tee /etc/plymouth/plymouthd.conf
    # keep the splash on screen until the app draws over it
    sudo mkdir -p /etc/systemd/system/plymouth-quit.service.d
    sudo cp plymouth-quit-retain.conf /etc/systemd/system/plymouth-quit.service.d/retain.conf
    sudo systemctl daemon-reload
    # /boot/firmware/cmdline.txt: append
    #   quiet splash plymouth.ignore-serial-consoles logo.nologo vt.global_cursor_default=0 loglevel=3
    # /boot/firmware/config.txt: add
    #   disable_splash=1
    sudo update-initramfs -u

Undo: remove the cmdline.txt options and `disable_splash=1`, then
`sudo apt remove plymouth && sudo update-initramfs -u`.

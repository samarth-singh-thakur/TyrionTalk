This snapshot contains:
- /opt/bt-call-bridge
- /etc/systemd/system/bt-call-bridge.service
- /var/lib/bluetooth
- /var/lib/bluealsa
- package and service state captures

Typical restore flow:
1. Reinstall required packages if needed.
2. Restore /opt/bt-call-bridge
3. Restore bt-call-bridge.service
4. Restore /var/lib/bluetooth and /var/lib/bluealsa
5. Run systemctl daemon-reload
6. Enable/restart bt-call-bridge.service
7. Stop conflicting user audio services if needed:
   systemctl --user stop pipewire pipewire-pulse wireplumber

If the phone still refuses call audio after restore, delete and re-pair once.

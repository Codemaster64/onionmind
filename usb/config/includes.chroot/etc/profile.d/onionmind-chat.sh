# tty1 = the console you land on: straight into the chat, once. Quit the chat
# and you get a normal shell on the same tty; the flag keeps getty's respawn
# from dropping you straight back into the chat after you exit it.
if [ "$(tty 2>/dev/null)" = "/dev/tty1" ] && [ ! -e /run/onionmind-chat-done ] && [ -z "${ONIONMIND_CHAT:-}" ]; then
  ONIONMIND_CHAT=1
  touch /run/onionmind-chat-done
  /usr/local/bin/onionmind
fi

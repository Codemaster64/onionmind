# The onionmind-* helpers live in /usr/local/sbin (root territory); the
# console user needs them on PATH to find them at all.
case ":$PATH:" in
  *:/usr/local/sbin:*) ;;
  *) export PATH="$PATH:/usr/local/sbin" ;;
esac

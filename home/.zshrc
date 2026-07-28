source ~/.config/zsh/cachyos-config-custom.zsh

# --- illogical-impulse integration ---
source ~/.config/zshrc.d/dots-hyprland.zsh   # matches terminal colors to current wallpaper/theme
source ~/.config/zshrc.d/shortcuts.zsh       # ctrl+h / ctrl+z keybinds

# kitty doesn't clear its scrollback properly with plain `clear`
alias clear="printf '\033[2J\033[3J\033[1;1H'"
alias q='qs -c ii'   # reload/inspect the quickshell config
if [[ "$TERM" != "linux" ]]; then
  alias ls='eza --icons=auto'
fi
if [[ "$TERM" == "xterm-kitty" ]]; then
  alias ssh='kitten ssh'
fi

unsetopt CORRECT_ALL

# Added by LM Studio CLI (lms)
export PATH="$PATH:/home/henrique/.lmstudio/bin"
# End of LM Studio CLI section


require "nvchad.mappings"

-- add yours here

local map = vim.keymap.set

map("n", ";", ":", { desc = "CMD enter command mode" })
map("i", "jk", "<ESC>")
map("n", "<leader>us", "<cmd>setlocal spell! spell?<CR>", { desc = "Toggle spell check" })
map("n", "<leader>ua", "<cmd>AutosaveToggle<CR>", { desc = "Toggle autosave" })

-- map({ "n", "i", "v" }, "<C-s>", "<cmd> w <cr>")

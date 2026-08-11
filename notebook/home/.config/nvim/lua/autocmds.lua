require "nvchad.autocmds"

vim.api.nvim_create_autocmd("FileType", {
  pattern = { "markdown", "gitcommit", "gitrebase" },
  callback = function()
    vim.opt_local.spell = true
    vim.opt_local.spelllang = { "en_us", "pt_br" }
  end,
})

vim.g.autosave_enabled = true

vim.api.nvim_create_user_command("AutosaveToggle", function()
  vim.g.autosave_enabled = not vim.g.autosave_enabled
  local state = vim.g.autosave_enabled and "ON" or "OFF"
  vim.notify("Autosave: " .. state)
end, {})

vim.api.nvim_create_autocmd({ "InsertLeave", "TextChanged", "FocusLost" }, {
  callback = function()
    if not vim.g.autosave_enabled then
      return
    end

    if vim.bo.buftype ~= "" or not vim.bo.modifiable or vim.bo.readonly then
      return
    end

    if vim.bo.modified and vim.fn.expand "%" ~= "" then
      vim.cmd "silent! write"
    end
  end,
})

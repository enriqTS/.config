require("nvchad.configs.lspconfig").defaults()

local servers = {
  -- JavaScript / TypeScript
  "ts_ls",
  -- Python
  "pyright",
  -- C / C++
  "clangd",
  -- Terraform
  "terraformls",
  -- JSON
  "jsonls",
  -- Dockerfile
  "dockerls",
  -- Markdown
  "marksman",
}

vim.lsp.enable(servers)

-- read :h vim.lsp.config for changing options of lsp servers 

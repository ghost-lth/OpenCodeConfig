import { Plugin } from "@opencode/plugin"
import { execFile } from "node:child_process"
import { mkdir, rm } from "node:fs/promises"
import { promisify } from "node:util"

const run = promisify(execFile)
const socket = "/Volumes/ExternalSSD/opencode/tmp/opencode/editor-live/nvim.sock"
const nvim = "/opt/homebrew/bin/nvim"

async function isRunning() {
  try {
    await run(nvim, ["--server", socket, "--remote-expr", "1"], { timeout: 1000 })
    return true
  } catch {
    return false
  }
}

export default Plugin.define({
  id: "editor-tab",
  async setup(ctx) {
    const hook = await ctx.tool.hook("execute.before", async ({ tool, input }) => {
      const args = input as { recover?: boolean; reconnect?: boolean }
      if (tool !== "editor_bind" || args.recover || args.reconnect || await isRunning()) return

      await mkdir(socket.slice(0, socket.lastIndexOf("/")), { recursive: true })
      await rm(socket, { force: true })
      const kitty = process.env.KITTY_LISTEN_ON
      if (!kitty) throw new Error("KITTY_LISTEN_ON is unavailable")
      await run("kitty", [
        "@", "--to", kitty, "--use-password=always", "launch", "--self", "--type=tab", "--dont-take-focus",
        "--tab-title=OpenCode Editor", "--cwd=current", "/bin/zsh", "-lc", `exec ${nvim} --listen ${socket}`,
      ], { timeout: 5000 })

      for (let i = 0; i < 20 && !await isRunning(); i++) {
        await new Promise((resolve) => setTimeout(resolve, 100))
      }
      if (!await isRunning()) throw new Error("OpenCode Editor tab did not start Neovim")
    })
    return hook.dispose
  },
})

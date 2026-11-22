// Run with Bun; validates the actual documented filters without contacting a device.
import { strict } from "node:assert"

const skill = await Bun.file(`${import.meta.dir}/SKILL.md`).text()
const filters = [...skill.matchAll(/\| jq -e '([^']+)'/g)].map((match) => match[1])
strict.equal(filters.length, 2)
strict.equal((skill.match(/--connect-timeout 15 --max-time 25/g) ?? []).length, 5)
strict(!skill.includes("--connect-timeout 5"))
for (const [index, filter] of filters.entries()) {
  const valid = index === 0
    ? [{ current_app: null, last_error: null }, { current_app: { id: "clock" }, last_error: null }, { current_app: null }]
    : [{ ok: true, apps: [] }, { ok: true, apps: [{ id: "clock", name: "Clock" }] }]
  const invalid = index === 0
    ? [{ last_error: null }, { current_app: {}, last_error: null },
      { ok: false, current_app: null, last_error: null }]
    : [{ ok: false, apps: [] }, { ok: true }, { ok: true, apps: [{}] }]
  for (const [inputs, expected] of [[valid.map((value) => JSON.stringify(value)), true],
    [["", "<html>", "null", "[]", "{}", ...invalid.map((value) => JSON.stringify(value))], false]] as const) {
    for (const input of inputs) {
      const result = Bun.spawnSync(["jq", "-e", filter], { stdin: Buffer.from(input) })
      strict.equal(result.exitCode === 0, expected, input)
    }
  }
}
const pipeline = Bun.spawnSync(["zsh", "-c", "set -o pipefail; (exit 28) | jq -n '{}'" ])
strict.notEqual(pipeline.exitCode, 0)
console.log("PASS: documented filters reject invalid/empty responses; pipefail preserves failure")

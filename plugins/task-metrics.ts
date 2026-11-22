import { Plugin } from "@opencode/plugin"
import { appendFile, mkdir } from "node:fs/promises"
import { dirname } from "node:path"

const logPath = "/Volumes/ExternalSSD/opencode/data/opencode/log/stream-monitor.jsonl"

const log = (event: string, details: Record<string, unknown>) => {
  void mkdir(dirname(logPath), { recursive: true })
    .then(() => appendFile(logPath, `${JSON.stringify({ time: new Date().toISOString(), event, ...details })}\n`))
    .catch(console.error)
}

type Metrics = {
  userMessageID: string
  started: number
  prompt: number
  output: number
  modelMs: number
  retries: number
  assistantMessageID?: string
  firstOutput?: number
  providerBaseURL?: string
  requestIDs: string[]
  steps: Map<string, number>
  watchdog: ReturnType<typeof setTimeout>
}

export default Plugin.define({
  id: "task-metrics",
  async setup(ctx) {
    const sessions = new Map<string, Metrics>()
    const controller = new AbortController()

    const prompt = await ctx.session.hook("prompt", ({ sessionID, messageID }) => {
      const current = sessions.get(sessionID)
      if (current) {
        clearTimeout(current.watchdog)
        log("request_superseded", {
          sessionID,
          userMessageID: current.userMessageID,
          elapsedMs: Date.now() - current.started,
          receivedOutput: current.firstOutput !== undefined,
        })
      }

      const metrics: Metrics = {
        userMessageID: messageID,
        started: Date.now(),
        prompt: 0,
        output: 0,
        modelMs: 0,
        retries: 0,
        requestIDs: [],
        steps: new Map(),
        watchdog: setTimeout(() => {
          if (sessions.get(sessionID) !== metrics || metrics.firstOutput) return
          log("no_model_output_30s", {
            sessionID,
            userMessageID: messageID,
            assistantMessageID: metrics.assistantMessageID,
            requestIDs: metrics.requestIDs,
          })
          if (!metrics.providerBaseURL) return
          const started = Date.now()
          void fetch(metrics.providerBaseURL, { method: "HEAD", signal: AbortSignal.timeout(10000) })
            .then((response) => log("provider_connectivity_probe", {
              sessionID,
              host: new URL(metrics.providerBaseURL!).host,
              elapsedMs: Date.now() - started,
              status: response.status,
            }))
            .catch((error) => log("provider_connectivity_probe_failed", {
              sessionID,
              host: new URL(metrics.providerBaseURL!).host,
              elapsedMs: Date.now() - started,
              error: error instanceof Error ? `${error.name}: ${error.message}` : String(error),
            }))
        }, 30000),
      }
      sessions.set(sessionID, metrics)
      log("request_started", { sessionID, userMessageID: messageID })
    })

    const request = await ctx.session.hook("model.request", (input) => {
      if (input.kind !== "primary") return
      const metrics = sessions.get(input.sessionID)
      const requestID = input.headers["x-ms-client-request-id"] ?? crypto.randomUUID()
      input.headers["x-ms-client-request-id"] = requestID
      if (metrics) {
        metrics.requestIDs.push(requestID)
        metrics.providerBaseURL = input.baseURL
      }
      log("provider_request_dispatched", {
        sessionID: input.sessionID,
        userMessageID: metrics?.userMessageID,
        requestID,
        providerID: input.model.providerID,
        modelID: input.model.id,
        agent: input.agent,
      })
    })

    const events = async () => {
      for await (const event of ctx.event.subscribe({
        signal: controller.signal,
        types: [
          "session.step.started", "session.step.first-token", "session.step.ended", "session.step.failed",
          "session.retry.scheduled", "session.execution.succeeded", "session.execution.failed",
          "session.execution.interrupted",
        ],
      })) {
        const metrics = sessions.get(event.data.sessionID)
        if (!metrics) continue

        if (event.type === "session.step.started") {
          metrics.assistantMessageID ??= event.data.assistantMessageID
          metrics.steps.set(event.data.assistantMessageID, event.data.started)
          log("assistant_created", event.data)
          continue
        }
        if (event.type === "session.step.first-token") {
          if (metrics.firstOutput) continue
          metrics.firstOutput = event.created
          clearTimeout(metrics.watchdog)
          log("first_model_output", {
            sessionID: event.data.sessionID,
            userMessageID: metrics.userMessageID,
            assistantMessageID: event.data.assistantMessageID,
            latencyMs: event.created - metrics.started,
          })
          continue
        }
        if (event.type === "session.step.ended" || event.type === "session.step.failed") {
          const tokens = event.data.tokens
          if (tokens) {
            metrics.prompt += tokens.input + tokens.cache.read + tokens.cache.write
            metrics.output += tokens.output + tokens.reasoning
          }
          const started = metrics.steps.get(event.data.assistantMessageID)
          if (started) metrics.modelMs += event.created - started
          log(event.type === "session.step.ended" ? "assistant_completed" : "assistant_failed", {
            sessionID: event.data.sessionID,
            assistantMessageID: event.data.assistantMessageID,
            elapsedMs: started ? event.created - started : undefined,
            finish: event.data.finish,
            error: event.type === "session.step.failed" ? event.data.error : undefined,
          })
          continue
        }
        if (event.type === "session.retry.scheduled") {
          metrics.retries = Math.max(metrics.retries, event.data.attempt)
          log("provider_retry", { ...event.data, userMessageID: metrics.userMessageID })
          continue
        }
        if (event.type === "session.execution.failed") log("session_error", event.data)
        if (event.type === "session.execution.interrupted" && event.data.reason === "superseded") continue

        sessions.delete(event.data.sessionID)
        clearTimeout(metrics.watchdog)
        log("request_idle", {
          sessionID: event.data.sessionID,
          userMessageID: metrics.userMessageID,
          assistantMessageID: metrics.assistantMessageID,
          totalMs: event.created - metrics.started,
          firstOutputMs: metrics.firstOutput ? metrics.firstOutput - metrics.started : null,
          promptTokens: metrics.prompt,
          outputTokens: metrics.output,
          modelMs: metrics.modelMs,
          retries: metrics.retries,
        })
      }
    }
    void events().catch((error) => {
      if (!controller.signal.aborted) console.error(error)
    })

    return async () => {
      controller.abort()
      sessions.forEach((metrics) => clearTimeout(metrics.watchdog))
      await Promise.all([prompt.dispose(), request.dispose()])
    }
  },
})

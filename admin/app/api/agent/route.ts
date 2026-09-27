/**
 * POST /api/agent — the producer agent. Streams Server-Sent Events:
 *   {type:"text", text}                       assistant text delta
 *   {type:"proposal", proposal}               a validated propose_config_patch call (diff card)
 *   {type:"turn", assistant, user}            content blocks to append to the client history
 *   {type:"done", stop_reason} | {type:"error", error}
 *
 * Manual streaming loop (not the beta tool runner) so each tool call can be dry-run against the
 * operator's pending YAML and rendered as a card before the model continues.
 */
import Anthropic from "@anthropic-ai/sdk";
import { z } from "zod";
import { errorResponse, requireUser } from "@/lib/auth";
import { describeConfig, describeRun, PRODUCER_INSTRUCTIONS } from "@/lib/agent-prompt";
import { buildProposal, ProposalInput } from "@/lib/proposals";
import { getStore } from "@/lib/store";

export const dynamic = "force-dynamic";
export const maxDuration = 300;

const MODEL = process.env.AGENT_MODEL || "claude-opus-5";
const MAX_ITERATIONS = 6;

const Body = z.object({
  messages: z.array(z.object({ role: z.enum(["user", "assistant"]), content: z.unknown() })).min(1).max(80),
  files: z.record(z.string(), z.string()),
  run_id: z.string().optional().nullable(),
});

const proposeConfigPatch = {
  name: "propose_config_patch",
  description:
    "Propose one coherent change to a config file as a YAML patch. It is shown to the operator as a diff card with an Apply button; it is NOT applied automatically. Use one call per change. Only files under config/ are allowed.",
  strict: true as const,
  eager_input_streaming: true,
  input_schema: {
    type: "object" as const,
    properties: {
      title: { type: "string", description: "Short imperative title, e.g. 'Make Claire warmer in the cold open'" },
      rationale: { type: "string", description: "Why, grounded in the logs/script/config. 1–3 sentences." },
      file: { type: "string", description: "Repo path, e.g. config/show.yaml" },
      ops: {
        type: "array",
        description: "Patch operations applied in order.",
        items: {
          type: "object",
          properties: {
            op: { type: "string", enum: ["set", "delete", "append"] },
            path: { type: "array", items: { anyOf: [{ type: "string" }, { type: "integer" }] }, description: "Keys and list indexes from the document root" },
            value: { description: "New value for set/append; null for delete. Any JSON." },
          },
          required: ["op", "path", "value"],
          additionalProperties: false,
        },
      },
    },
    required: ["title", "rationale", "file", "ops"],
    additionalProperties: false,
  },
};

export async function POST(req: Request) {
  try {
    await requireUser();
    if (!process.env.ANTHROPIC_API_KEY) return errorResponse(Object.assign(new Error("ANTHROPIC_API_KEY is not configured on the panel"), { status: 503 }));
    const body = Body.parse(await req.json());
    const client = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY, maxRetries: 2 });

    // Run context (server-side, so the client never has to ship logs back and forth).
    let runContext = "No run selected.";
    if (body.run_id) {
      const store = getStore();
      const [run, events, script] = await Promise.all([store.getRun(body.run_id), store.getEvents(body.run_id, 0, 8000), store.getScript(body.run_id)]);
      runContext = describeRun(run, events, script);
    }

    const system: Anthropic.TextBlockParam[] = [
      { type: "text", text: PRODUCER_INSTRUCTIONS, cache_control: { type: "ephemeral" } },
      { type: "text", text: describeConfig(body.files) },
      { type: "text", text: runContext },
    ];

    const messages = body.messages as Anthropic.MessageParam[];
    const encoder = new TextEncoder();
    const files = body.files;

    const stream = new ReadableStream<Uint8Array>({
      async start(controller) {
        const send = (obj: unknown) => controller.enqueue(encoder.encode(`data: ${JSON.stringify(obj)}\n\n`));
        try {
          for (let iteration = 0; iteration < MAX_ITERATIONS; iteration++) {
            const msgStream = client.messages.stream({
              model: MODEL,
              max_tokens: 16000,
              system,
              tools: [proposeConfigPatch],
              messages,
            });
            msgStream.on("text", (delta) => send({ type: "text", text: delta }));
            const message = await msgStream.finalMessage();

            if (message.stop_reason === "refusal") {
              send({ type: "turn", assistant: message.content, user: null });
              send({ type: "error", error: "The model declined this request." });
              break;
            }
            const toolUses = message.content.filter((b): b is Anthropic.ToolUseBlock => b.type === "tool_use");
            if (message.stop_reason === "max_tokens" && toolUses.length) {
              send({ type: "turn", assistant: message.content, user: null });
              send({ type: "error", error: "The proposal was cut off (max_tokens). Ask again with a smaller change." });
              break;
            }
            if (!toolUses.length || message.stop_reason !== "tool_use") {
              send({ type: "turn", assistant: message.content, user: null });
              send({ type: "done", stop_reason: message.stop_reason });
              break;
            }

            const results: Anthropic.ToolResultBlockParam[] = [];
            for (const tu of toolUses) {
              if (tu.name !== "propose_config_patch") {
                results.push({ type: "tool_result", tool_use_id: tu.id, is_error: true, content: `Unknown tool ${tu.name}` });
                continue;
              }
              // Eager input streaming: the SDK's tolerant parser may hand back a truncated object → validate.
              const parsed = ProposalInput.safeParse(tu.input);
              if (!parsed.success) {
                results.push({ type: "tool_result", tool_use_id: tu.id, is_error: true, content: JSON.stringify({ INVALID_JSON: JSON.stringify(tu.input), issues: parsed.error.issues.map((i) => `${i.path.join(".")}: ${i.message}`) }) });
                continue;
              }
              const proposal = buildProposal(parsed.data, files);
              send({ type: "proposal", proposal, tool_use_id: tu.id });
              if (proposal.error) {
                results.push({ type: "tool_result", tool_use_id: tu.id, is_error: true, content: `Patch rejected: ${proposal.error}. Fix the path/value and try again.` });
              } else {
                results.push({ type: "tool_result", tool_use_id: tu.id, content: `Shown to the operator as a diff card (they decide whether to apply it). Resulting diff:\n${proposal.diff.slice(0, 6000)}` });
              }
            }
            const userTurn: Anthropic.MessageParam = { role: "user", content: results };
            messages.push({ role: "assistant", content: message.content }, userTurn);
            send({ type: "turn", assistant: message.content, user: userTurn.content });
          }
        } catch (e) {
          const msg = e instanceof Anthropic.APIError ? `Claude API error ${e.status}: ${e.message}` : (e as Error).message;
          send({ type: "error", error: msg });
        } finally {
          controller.close();
        }
      },
    });

    return new Response(stream, { headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache, no-transform", Connection: "keep-alive" } });
  } catch (e) {
    return errorResponse(e);
  }
}

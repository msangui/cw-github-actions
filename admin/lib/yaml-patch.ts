/**
 * Comment-preserving YAML edits. Every change the panel makes — a slider, a toggle, a prompt
 * edit, or a patch proposed by the producer agent — is expressed as a list of PatchOps applied
 * to the file's YAML Document, so comments and key order in the repo files survive.
 */
import { Document, isMap, isScalar, isSeq, parseDocument, Scalar, YAMLSeq } from "yaml";
import { z } from "zod";

export const PathSegment = z.union([z.string(), z.number().int()]);
export const PatchOp = z.discriminatedUnion("op", [
  z.object({ op: z.literal("set"), path: z.array(PathSegment).min(1), value: z.unknown() }),
  z.object({ op: z.literal("delete"), path: z.array(PathSegment).min(1) }),
  z.object({ op: z.literal("append"), path: z.array(PathSegment).min(1), value: z.unknown() }),
]);
export type PatchOp = z.infer<typeof PatchOp>;

export const STRINGIFY_OPTS = { lineWidth: 0, blockQuote: "literal" as const };

export function parseYaml<T = unknown>(text: string): T {
  const doc = parseDocument(text);
  if (doc.errors.length) throw new Error(doc.errors.map((e) => e.message).join("; "));
  return doc.toJS() as T;
}

function setPreservingStyle(doc: Document, path: (string | number)[], value: unknown) {
  const existing = doc.getIn(path, true);
  const primitive = value === null || ["string", "number", "boolean"].includes(typeof value);
  if (isScalar(existing) && primitive) {
    // Keep `|` block scalars, quoting style, and trailing comments on the node.
    const scalar = existing as Scalar;
    if (typeof value === "string" && value.includes("\n") && scalar.type !== Scalar.BLOCK_LITERAL && scalar.type !== Scalar.BLOCK_FOLDED) {
      scalar.type = Scalar.BLOCK_LITERAL;
    }
    scalar.value = value;
    return;
  }
  doc.setIn(path, doc.createNode(value));
}

/** Apply ops to YAML text and return the new text. Throws on invalid YAML or an impossible op. */
export function applyPatch(text: string, ops: PatchOp[]): string {
  const doc = parseDocument(text);
  if (doc.errors.length) throw new Error(`Invalid YAML: ${doc.errors[0].message}`);
  for (const op of ops) {
    const path = [...op.path];
    if (op.op === "set") {
      setPreservingStyle(doc, path, op.value);
    } else if (op.op === "delete") {
      if (!doc.deleteIn(path)) throw new Error(`delete: nothing at ${path.join(".")}`);
    } else if (op.op === "append") {
      const node = doc.getIn(path, true);
      if (node === undefined) {
        doc.setIn(path, doc.createNode([op.value]));
      } else if (isSeq(node)) {
        (node as YAMLSeq).add(doc.createNode(op.value));
      } else {
        throw new Error(`append: ${path.join(".")} is not a list`);
      }
    }
  }
  const out = doc.toString(STRINGIFY_OPTS);
  // Round-trip check: the result must parse and the pipeline's PyYAML must be able to read it.
  const check = parseDocument(out);
  if (check.errors.length) throw new Error(`Patch produced invalid YAML: ${check.errors[0].message}`);
  return out;
}

export function getAt(obj: unknown, path: (string | number)[]): unknown {
  let cur: unknown = obj;
  for (const seg of path) {
    if (cur === null || cur === undefined) return undefined;
    cur = (cur as Record<string | number, unknown>)[seg];
  }
  return cur;
}

export function isMapNode(x: unknown) {
  return isMap(x);
}

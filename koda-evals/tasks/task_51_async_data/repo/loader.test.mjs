import { loadData } from "./loader.js";
import assert from "node:assert";

// Mock fetch
global.fetch = async (url) => {
  if (url === "/good") {
    return { json: async () => ({ ok: true }) };
  }
  if (url === "/bad") {
    return { json: async () => { throw new Error("not json"); } };
  }
  throw new Error("network");
};

{
  const data = await loadData("/good");
  assert.deepStrictEqual(data, { ok: true });
}

{
  const data = await loadData("/bad");
  assert.deepStrictEqual(data, { error: "Invalid JSON" });
}

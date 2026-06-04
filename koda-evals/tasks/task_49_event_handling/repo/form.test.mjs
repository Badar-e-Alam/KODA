import { setupForm } from "./form.js";
import assert from "node:assert";

function mockForm(emailValue) {
  let prevented = false;
  let submitted = false;
  const listeners = {};
  return {
    querySelector(sel) {
      return { value: emailValue };
    },
    addEventListener(event, handler) {
      listeners[event] = handler;
    },
    submit() {
      submitted = true;
      if (listeners.submit) {
        const evt = { preventDefault() { prevented = true; } };
        const result = listeners.submit(evt);
        // If preventDefault was called, don't submit
        if (prevented) submitted = false;
      }
    },
    get submitted() { return submitted; },
  };
}

{
  const form = mockForm("bad");
  setupForm(form);
  form.submit();
  assert.strictEqual(form.submitted, false, "invalid form should not submit");
}

{
  const form = mockForm("good@example.com");
  setupForm(form);
  form.submit();
  assert.strictEqual(form.submitted, true, "valid form should submit");
}

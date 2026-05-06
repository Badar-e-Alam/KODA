export function setupForm(form) {
  form.addEventListener("submit", (event) => {
    const email = form.querySelector("[name=email]").value;
    if (!email.includes("@")) {
      return false;  // Bug: doesn't prevent default
    }
    return true;
  });
}

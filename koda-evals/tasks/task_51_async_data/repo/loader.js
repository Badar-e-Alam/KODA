export async function loadData(url) {
  const response = await fetch(url);
  const data = await response.json();  // Bug: no error handling
  return data;
}

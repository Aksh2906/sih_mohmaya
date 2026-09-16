/* Translate interface copy only. User input, page evidence and transcripts stay untouched. */
const VeilLocale = {
  language: localStorage.getItem("veil.language") === "hi" ? "hi" : "en",
  dictionary: {},
  nodes: [],
  attributes: [],
  t(value) {
    if (typeof value !== "string" || this.language !== "hi") return value;
    const key = value.replace(/\s+/g, " ").trim();
    return this.dictionary[key]
      ? value.match(/^\s*/)[0] + this.dictionary[key] + value.match(/\s*$/)[0]
      : value;
  },
  apply() {
    document.documentElement.lang = this.language;
    this.nodes.forEach(([node, source]) => {
      node.textContent = this.t(source);
    });
    this.attributes.forEach(([node, name, source]) =>
      node.setAttribute(name, this.t(source)),
    );
    document.getElementById("language").value = this.language;
    window.dispatchEvent(new Event("veil:language"));
  },
};
{
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const node = walker.currentNode;
    if (
      node.textContent.trim() &&
      !node.parentElement.closest("script,style,#language,pre,textarea")
    )
      VeilLocale.nodes.push([node, node.textContent]);
  }
  document
    .querySelectorAll("[placeholder],[title],[aria-label],[alt]")
    .forEach((node) => {
      for (const name of ["placeholder", "title", "aria-label", "alt"])
        if (node.hasAttribute(name))
          VeilLocale.attributes.push([node, name, node.getAttribute(name)]);
    });
  document.getElementById("language").onchange = (event) => {
    VeilLocale.language = event.target.value;
    localStorage.setItem("veil.language", VeilLocale.language);
    VeilLocale.apply();
  };
  fetch(chrome.runtime.getURL("locales/hi.json"))
    .then((response) => response.json())
    .then((dictionary) => {
      VeilLocale.dictionary = dictionary;
      VeilLocale.apply();
    });
}

// Renders model/backend text WITHOUT ever touching innerHTML (Part D: output handling).
// Supports only: **bold**, "- " bullet lists, "---" rules and paragraphs.
// Anything else -- HTML tags, markdown images/links, scripts -- is shown as inert text.
function inline(line) {
  return line.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.length > 4 && part.startsWith("**") && part.endsWith("**") ? (
      <strong key={i}>{part.slice(2, -2)}</strong>
    ) : (
      part
    )
  );
}

export default function SafeText({ text }) {
  const lines = String(text ?? "").split(/\r?\n/);
  const out = [];
  let list = [];
  const flush = () => {
    if (list.length) {
      out.push(
        <ul key={`ul${out.length}`} style={{ margin: "6px 0 10px 18px", padding: 0 }}>
          {list.map((li, i) => (
            <li key={i}>{inline(li)}</li>
          ))}
        </ul>
      );
      list = [];
    }
  };
  lines.forEach((raw) => {
    const line = raw.trimEnd();
    if (/^\s*[-*]\s+/.test(line)) {
      list.push(line.replace(/^\s*[-*]\s+/, ""));
    } else if (/^-{3,}$/.test(line.trim())) {
      flush();
      out.push(<hr key={`hr${out.length}`} style={{ border: 0, borderTop: "1px solid var(--rule)", margin: "12px 0" }} />);
    } else if (line.trim() === "") {
      flush();
    } else if (list.length && /^\s{2,}\S/.test(raw)) {
      list[list.length - 1] += ` ${line.trim()}`; // wrapped continuation of a bullet
    } else {
      flush();
      out.push(
        <p key={`p${out.length}`} style={{ margin: "0 0 8px" }}>
          {inline(line)}
        </p>
      );
    }
  });
  flush();
  return <div>{out}</div>;
}

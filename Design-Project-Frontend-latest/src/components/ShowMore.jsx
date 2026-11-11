import { useState } from "react";

// Renders the first `initial` items of a long list, with a toggle for the rest.
export default function ShowMore({ items, initial = 8, children }) {
  const [open, setOpen] = useState(false);
  if (!items?.length) return null;
  const hidden = items.length - initial;
  const shown = open || hidden <= 0 ? items : items.slice(0, initial);
  return (
    <>
      {shown.map(children)}
      {hidden > 0 && (
        <button type="button" className="btn secondary show-more" onClick={() => setOpen((o) => !o)}>
          {open ? "Show fewer" : `Show ${hidden} more`}
        </button>
      )}
    </>
  );
}

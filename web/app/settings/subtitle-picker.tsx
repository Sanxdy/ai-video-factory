"use client";

/**
 * Caption preset picker: every preset shown as a real rendered preview, so the
 * choice is made by looking rather than by reading a name.
 *
 * The previews come from GET /api/settings/subtitle/preview, which runs the
 * actual libass burn the encoder runs — animated for the word-by-word presets,
 * whose single frame would otherwise all read as the same first word. An HTML
 * mock-up would be cheaper and would be wrong within one preset edit.
 *
 * A list of rows rather than a grid of frame cards: the row carries the name
 * and what the style does beside a thumbnail sized to the caption, so six
 * presets fit in the space one oversized card used to take.
 *
 * One component for both Settings and the wizard: two copies of this list
 * would drift the first time a preset was added.
 */
export default function SubtitlePicker({ presets, value, onChange, disabled = false }: {
  presets: { id: string; label: string }[];
  value: string;
  onChange: (id: string) => void;
  disabled?: boolean;
}) {
  return (
    <div className="sublist" role="radiogroup" aria-label="Caption preset">
      {presets.map((p) => {
        const on = p.id === value;
        const [name, desc] = p.label.split(" — ");
        return (
          <button
            key={p.id}
            type="button"
            role="radio"
            aria-checked={on}
            disabled={disabled}
            onClick={() => onChange(p.id)}
            className="subrow"
            data-on={on ? "1" : undefined}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={`/api/settings/subtitle/preview?preset=${p.id}`}
                 alt={`${name} — example caption`} loading="lazy" />
            <span className="submeta">
              <span className="t">{name}</span>
              {desc ? <span className="d">{desc}</span> : null}
            </span>
          </button>
        );
      })}
    </div>
  );
}

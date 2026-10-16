"use client";

import { useState } from "react";

// No look-alike characters (0/O, 1/l/I) so it's easy to read out or retype.
const ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789";

function suggest(length = 14) {
  const bytes = new Uint32Array(length);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => ALPHABET[b % ALPHABET.length]).join("");
}

/** Password input with a "Suggest" button; visible so the admin can copy it. */
export function PasswordField({
  id, name = "password", label = "Password", placeholder, required = false,
}: { id: string; name?: string; label?: string; placeholder?: string; required?: boolean }) {
  const [value, setValue] = useState("");
  return (
    <div className="min-w-56 flex-1">
      <label className="label" htmlFor={id}>{label}</label>
      <div className="flex gap-2">
        <input
          id={id} name={name} type="text" autoComplete="new-password" spellCheck={false}
          minLength={8} required={required} value={value} placeholder={placeholder}
          onChange={(e) => setValue(e.target.value)} className="input font-mono"
        />
        <button type="button" className="btn shrink-0" onClick={() => setValue(suggest())}>Suggest</button>
      </div>
    </div>
  );
}

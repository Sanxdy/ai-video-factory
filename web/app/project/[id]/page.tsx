import Shell from "./shell";

// Static export needs prerenderable params; the client shell reads the real
// id from the URL at runtime (FastAPI serves this shell for any /project/<id>).
export function generateStaticParams() {
  return [{ id: "0" }];
}

export default function Page() {
  return <Shell />;
}

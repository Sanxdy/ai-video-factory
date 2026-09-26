/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "export",           // static export → served by FastAPI
  images: { unoptimized: true },
  // dynamic client route without generateStaticParams: export a shell page
  // (static export can't prerender [id] — FastAPI serves index.html fallback)
  trailingSlash: false,
};

export default nextConfig;

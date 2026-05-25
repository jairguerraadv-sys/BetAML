import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export function GET() {
  const response = NextResponse.json({
    status: "live",
    ready: true,
    service: "frontend",
    timestamp: new Date().toISOString(),
  });
  response.headers.set("Cache-Control", "no-store, no-cache, must-revalidate");
  return response;
}

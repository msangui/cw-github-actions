import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";

// The pipeline posts here with a bearer token (checked in the route); everything else is Clerk.
const isPipelineRoute = createRouteMatcher(["/api/runs/(.*)/events"]);
const isPublicRoute = createRouteMatcher(["/sign-in(.*)", "/api/health"]);

export default clerkMiddleware(async (auth, req) => {
  if (isPipelineRoute(req) || isPublicRoute(req)) return;
  await auth.protect();
});

export const config = {
  matcher: [
    // Skip Next.js internals and static files
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    "/(api|trpc)(.*)",
  ],
};

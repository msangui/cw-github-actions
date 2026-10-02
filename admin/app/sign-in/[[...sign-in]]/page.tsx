import { SignIn } from "@clerk/nextjs";

export default function SignInPage() {
  return (
    <main className="min-h-screen flex flex-col items-center justify-center gap-6 p-6">
      <div className="text-center">
        <div className="text-2xl font-bold tracking-tight">Context Window</div>
        <div className="text-muted">Producer panel · sign in with your Google Workspace account</div>
      </div>
      <SignIn />
    </main>
  );
}

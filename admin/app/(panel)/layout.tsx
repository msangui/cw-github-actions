"use client";

import { useState } from "react";
import { AgentDrawer } from "@/components/AgentDrawer";
import { ConfigProvider, useConfig } from "@/components/ConfigProvider";
import { Nav } from "@/components/Nav";
import { PendingBar } from "@/components/PendingBar";

export default function PanelLayout({ children }: { children: React.ReactNode }) {
  return (
    <ConfigProvider>
      <Shell>{children}</Shell>
    </ConfigProvider>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  const [agentOpen, setAgentOpen] = useState(false);
  const cfg = useConfig();
  return (
    <div className="min-h-screen pb-16">
      <Nav onToggleAgent={() => setAgentOpen((o) => !o)} agentOpen={agentOpen} />
      {(cfg.error || cfg.notice) && (
        <div className={`mx-4 mt-3 card p-3 text-[13px] flex items-start gap-3 ${cfg.error ? "border-err/40" : "border-warn/40"}`}>
          <span className={cfg.error ? "text-err" : "text-warn"}>{cfg.error ?? cfg.notice}</span>
          <div className="ml-auto flex gap-2">
            {cfg.error && (
              <button className="btn btn-sm" onClick={() => void cfg.reload()}>
                Retry
              </button>
            )}
            <button className="btn btn-sm" onClick={cfg.dismissNotice}>
              Dismiss
            </button>
          </div>
        </div>
      )}
      <main className={`p-4 transition-[padding] ${agentOpen ? "lg:pr-[37rem]" : ""}`}>{cfg.loading && !cfg.base ? <div className="text-muted p-8 text-center">Loading config from GitHub…</div> : children}</main>
      <PendingBar />
      <AgentDrawer open={agentOpen} onClose={() => setAgentOpen(false)} />
    </div>
  );
}

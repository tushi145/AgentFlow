import { PanelLeftClose, PanelLeftOpen, Workflow } from "lucide-react";
import { NavLink } from "react-router";

import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useSystemStatus } from "@/hooks/use-system";
import { NAV_SECTIONS } from "@/layouts/nav";
import { cn } from "@/lib/utils";

export function Logo({ collapsed = false }: { collapsed?: boolean }) {
  return (
    <div className="flex items-center gap-2">
      <div className="flex size-7 items-center justify-center rounded-lg bg-primary text-primary-foreground shadow-sm">
        <Workflow className="size-4" aria-hidden />
      </div>
      {!collapsed && <span className="text-[15px] font-semibold tracking-tight">AgentFlow</span>}
    </div>
  );
}

export function SidebarNav({ collapsed = false, onNavigate }: { collapsed?: boolean; onNavigate?: () => void }) {
  return (
    <nav aria-label="Main" className="flex flex-col gap-5">
      {NAV_SECTIONS.map((section) => (
        <div key={section.title} className="flex flex-col gap-0.5">
          {!collapsed && (
            <div className="px-2.5 pb-1 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/70">
              {section.title}
            </div>
          )}
          {section.items.map((item) => {
            const link = (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                onClick={onNavigate}
                className={({ isActive }) =>
                  cn(
                    "group flex h-8 items-center gap-2.5 rounded-md px-2.5 text-sm text-sidebar-foreground transition-colors",
                    "hover:bg-sidebar-active hover:text-foreground focus-visible:outline-2",
                    isActive && "bg-sidebar-active font-medium text-foreground",
                    collapsed && "justify-center px-0",
                  )
                }
              >
                <item.icon className="size-4 shrink-0 opacity-80 group-hover:opacity-100" aria-hidden />
                {collapsed ? <span className="sr-only">{item.label}</span> : <span className="truncate">{item.label}</span>}
              </NavLink>
            );
            return collapsed ? (
              <Tooltip key={item.to}>
                <TooltipTrigger asChild>{link}</TooltipTrigger>
                <TooltipContent side="right">{item.label}</TooltipContent>
              </Tooltip>
            ) : (
              link
            );
          })}
        </div>
      ))}
    </nav>
  );
}

export function Sidebar({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  const status = useSystemStatus();
  return (
    <aside
      className={cn(
        "sticky top-0 hidden h-svh shrink-0 flex-col border-r border-sidebar-border bg-sidebar transition-[width] duration-200 lg:flex",
        collapsed ? "w-[60px]" : "w-60",
      )}
    >
      <div className={cn("flex h-14 items-center px-4", collapsed && "justify-center px-0")}>
        <Logo collapsed={collapsed} />
      </div>
      <div className="flex-1 overflow-y-auto px-2.5 py-2">
        <SidebarNav collapsed={collapsed} />
      </div>
      <div className={cn("flex items-center gap-2 border-t border-sidebar-border p-2.5", collapsed ? "justify-center" : "justify-between")}>
        {!collapsed && (
          <span className="truncate px-1 font-mono text-[11px] text-muted-foreground">
            {status.data ? `v${status.data.version} · ${status.data.app_env}` : ""}
          </span>
        )}
        <Button variant="ghost" size="icon" className="size-7" onClick={onToggle}
                aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}>
          {collapsed ? <PanelLeftOpen className="size-4" /> : <PanelLeftClose className="size-4" />}
        </Button>
      </div>
    </aside>
  );
}

import { Bell, LogOut, Menu, Monitor, Moon, Sun, User as UserIcon } from "lucide-react";
import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router";

import { StatusDot } from "@/components/status";
import { useTheme } from "@/components/theme-provider";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuRadioGroup,
  DropdownMenuRadioItem, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useCurrentUser, useLogout } from "@/hooks/use-auth";
import { usePendingApprovals, useSystemStatus } from "@/hooks/use-system";
import { titleForPath } from "@/layouts/nav";
import { Logo, SidebarNav } from "@/layouts/Sidebar";

function SystemPill() {
  const { data, isLoading, isError } = useSystemStatus();
  if (isLoading) return null;
  if (isError || !data) {
    return (
      <span className="hidden items-center gap-2 rounded-full border px-2.5 py-1 text-xs text-muted-foreground sm:flex">
        <StatusDot tone="danger" /> API unreachable
      </span>
    );
  }
  const live = data.live_mode_available;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Link to="/settings" className="hidden items-center gap-2 rounded-full border px-2.5 py-1 text-xs hover:bg-accent sm:flex">
          <StatusDot tone={live ? "success" : "warning"} />
          {live ? "Live AI ready" : "Demo only"}
        </Link>
      </TooltipTrigger>
      <TooltipContent className="max-w-72">
        {live
          ? `Live execution available (${data.default_llm_provider} + ${data.search_provider.name}).`
          : `Live execution unavailable — missing ${data.missing_for_live.join(", ")}. Tasks can run in demo mode (templates, not AI).`}
      </TooltipContent>
    </Tooltip>
  );
}

function ApprovalsButton() {
  const { data } = usePendingApprovals();
  const n = data?.length ?? 0;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button asChild variant="ghost" size="icon" className="relative size-8">
          <Link to="/executions" aria-label={`${n} pending approval${n === 1 ? "" : "s"}`}>
            <Bell className="size-4" />
            {n > 0 && (
              <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-status-warning px-1 text-[10px] font-semibold text-black">
                {n}
              </span>
            )}
          </Link>
        </Button>
      </TooltipTrigger>
      <TooltipContent>{n ? `${n} approval${n === 1 ? "" : "s"} waiting` : "No pending approvals"}</TooltipContent>
    </Tooltip>
  );
}

function ThemeMenu() {
  const { theme, resolved, setTheme } = useTheme();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" className="size-8" aria-label="Change theme">
          {resolved === "dark" ? <Moon className="size-4" /> : <Sun className="size-4" />}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-36">
        <DropdownMenuRadioGroup value={theme} onValueChange={(v) => setTheme(v as typeof theme)}>
          <DropdownMenuRadioItem value="dark"><Moon className="size-3.5" /> Dark</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="light"><Sun className="size-3.5" /> Light</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="system"><Monitor className="size-3.5" /> System</DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function UserMenu() {
  const { data: user } = useCurrentUser();
  const logout = useLogout();
  const navigate = useNavigate();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" className="size-8 rounded-full" aria-label="Account">
          <span className="flex size-7 items-center justify-center rounded-full bg-muted text-xs font-semibold uppercase">
            {user?.username?.[0] ?? <UserIcon className="size-3.5" />}
          </span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-48">
        <DropdownMenuLabel className="font-normal">
          <div className="text-xs text-muted-foreground">Signed in as</div>
          <div className="truncate font-medium">{user?.username}</div>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => logout.mutate(undefined, { onSettled: () => navigate("/login") })}>
          <LogOut className="size-3.5" /> Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function TopBar() {
  const { pathname } = useLocation();
  const [open, setOpen] = useState(false);
  return (
    <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b bg-background/80 px-4 backdrop-blur supports-[backdrop-filter]:bg-background/60 sm:px-6">
      <Button variant="ghost" size="icon" className="size-8 lg:hidden" onClick={() => setOpen(true)} aria-label="Open navigation">
        <Menu className="size-4" />
      </Button>
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent side="left" className="w-64 bg-sidebar p-0">
          <SheetHeader className="h-14 justify-center px-4">
            <SheetTitle><Logo /></SheetTitle>
          </SheetHeader>
          <div className="px-2.5"><SidebarNav onNavigate={() => setOpen(false)} /></div>
        </SheetContent>
      </Sheet>
      <div className="min-w-0 flex-1 truncate text-sm font-medium">{titleForPath(pathname)}</div>
      <div className="flex items-center gap-1.5">
        <SystemPill />
        <ApprovalsButton />
        <ThemeMenu />
        <UserMenu />
      </div>
    </header>
  );
}

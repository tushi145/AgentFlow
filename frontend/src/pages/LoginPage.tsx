import { Loader2 } from "lucide-react";
import { type FormEvent, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useCurrentUser, useLogin } from "@/hooks/use-auth";
import { Logo } from "@/layouts/Sidebar";
import { errorMessage } from "@/services/api";

export default function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const me = useCurrentUser();
  const login = useLogin();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const from = (location.state as { from?: string } | null)?.from ?? "/";

  if (me.data) return <Navigate to={from} replace />;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    login.mutate({ username, password }, { onSuccess: () => navigate(from, { replace: true }) });
  };

  return (
    <div className="relative flex min-h-svh items-center justify-center overflow-hidden px-4">
      <div aria-hidden className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_top,color-mix(in_oklch,var(--primary)_18%,transparent),transparent_60%)]" />
      <div className="relative w-full max-w-sm space-y-6">
        <div className="flex justify-center"><Logo /></div>
        <Card>
          <CardHeader>
            <CardTitle><h1 className="text-lg">Sign in</h1></CardTitle>
            <CardDescription>Autonomous multi-agent research platform</CardDescription>
          </CardHeader>
          <CardContent>
            <form className="space-y-4" onSubmit={submit}>
              <div className="space-y-2">
                <Label htmlFor="username">Username</Label>
                <Input id="username" autoComplete="username" value={username} required
                       onChange={(e) => setUsername(e.target.value)} />
              </div>
              <div className="space-y-2">
                <Label htmlFor="password">Password</Label>
                <Input id="password" type="password" autoComplete="current-password" value={password} required
                       onChange={(e) => setPassword(e.target.value)} />
              </div>
              {login.error && (
                <p role="alert" className="text-sm text-status-danger">{errorMessage(login.error)}</p>
              )}
              <Button type="submit" className="w-full" disabled={login.isPending}>
                {login.isPending && <Loader2 className="size-4 animate-spin" />} Sign in
              </Button>
            </form>
          </CardContent>
        </Card>
        <p className="text-center text-xs text-muted-foreground">
          Single-user local account — configured with <code className="font-mono">ADMIN_USERNAME</code> in <code className="font-mono">.env</code>.
        </p>
      </div>
    </div>
  );
}

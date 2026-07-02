'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { Sparkles, Loader2, Brain, Shield, Zap } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { createClient } from '@/lib/supabase/client';
import { toast } from 'sonner';

const pills = [
  { icon: Brain, label: 'Semantic AI — meaning over keywords' },
  { icon: Shield, label: 'Honeypot & fraud detection built in' },
  { icon: Zap, label: 'Rank 100K candidates in under 5 min' },
];

export default function LoginForm() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [googleLoading, setGoogleLoading] = useState(false);
  const router = useRouter();
  const searchParams = useSearchParams();
  const redirect = searchParams.get('redirect') || '/dashboard';

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    const supabase = createClient();
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    setLoading(false);
    if (error) { toast.error(error.message); return; }
    toast.success('Welcome back!');
    router.push(redirect);
    router.refresh();
  };

  const handleGoogleLogin = async () => {
    setGoogleLoading(true);
    const supabase = createClient();
    const { error } = await supabase.auth.signInWithOAuth({
      provider: 'google',
      options: {
        redirectTo: `${window.location.origin}/auth/callback?next=${encodeURIComponent(redirect)}`,
      },
    });
    if (error) {
      toast.error(error.message);
      setGoogleLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex bg-[#0a0a0f]">

      {/* ── Left panel ──────────────────────────────────────────────────────── */}
      <div className="hidden lg:flex lg:w-1/2 relative overflow-hidden flex-col justify-between p-12">
        <div className="absolute inset-0 bg-gradient-to-br from-indigo-600 via-purple-700 to-indigo-900" />
        <div className="absolute top-[-80px] right-[-80px] w-[400px] h-[400px] rounded-full bg-white/5 border border-white/10" />
        <div className="absolute top-[60px] right-[60px] w-[250px] h-[250px] rounded-full bg-white/5 border border-white/10" />
        <div className="absolute bottom-[-100px] left-[-60px] w-[350px] h-[350px] rounded-full bg-purple-400/15 border border-white/8" />
        <div className="absolute bottom-[120px] left-[120px] w-[180px] h-[180px] rounded-full bg-indigo-400/10 border border-white/8" />
        <div className="absolute top-1/3 right-1/4 w-64 h-64 bg-purple-300/20 rounded-full blur-[80px]" />
        <div className="absolute bottom-1/4 left-1/3 w-48 h-48 bg-indigo-300/20 rounded-full blur-[60px]" />

        <div className="relative z-10 flex flex-col justify-between h-full">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-white/20 backdrop-blur flex items-center justify-center border border-white/30 shadow-lg">
              <Sparkles className="w-5 h-5 text-white" />
            </div>
            <span className="text-xl font-bold text-white tracking-wide">HireMind AI</span>
          </div>

          <div className="space-y-8">
            <div>
              <p className="text-white/50 text-sm font-medium uppercase tracking-widest mb-4">Welcome Back</p>
              <h2 className="text-6xl font-bold text-white leading-none mb-2">YOUR PIPELINE.</h2>

              <h2 className="text-6xl font-bold text-white/40 leading-none">AWAITS.</h2>
            </div>
            <p className="text-indigo-100/70 text-lg leading-relaxed max-w-sm">
              Pick up where you left off. Your projects, rankings, and candidate data are ready.
            </p>
            <div className="flex flex-col gap-3">
              {pills.map(f => (
                <div key={f.label} className="flex items-center gap-3 bg-white/10 backdrop-blur-sm border border-white/20 rounded-full px-4 py-2.5 w-fit">
                  <f.icon className="w-4 h-4 text-indigo-200 shrink-0" />
                  <span className="text-white/80 text-sm">{f.label}</span>
                </div>
              ))}
            </div>
          </div>


        </div>
      </div>

      {/* ── Right panel ───────────────────────────────────────────────────── */}
      <div className="flex-1 flex items-center justify-center p-8">
        <div className="w-full max-w-md">
          <div className="lg:hidden flex items-center gap-2 mb-8">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center">
              <Sparkles className="w-4 h-4 text-white" />
            </div>
            <span className="font-bold text-white">HireMind AI</span>
          </div>

          <h1 className="text-3xl font-bold mb-1 text-white">Welcome back</h1>
          <p className="text-white/40 mb-8">Sign in to your recruiter dashboard</p>

          {/* Google OAuth button */}
          <Button
            type="button"
            variant="outline"
            className="w-full h-11 bg-white/5 border-white/15 text-white hover:bg-white/10 hover:border-white/25 transition-all mb-4 flex items-center gap-3"
            onClick={handleGoogleLogin}
            disabled={googleLoading || loading}
          >
            {googleLoading ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <svg className="w-4 h-4 shrink-0" viewBox="0 0 24 24">
                <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" />
                <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
                <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" />
                <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" />
              </svg>
            )}
            Continue with Google
          </Button>

          {/* Divider */}
          <div className="relative mb-4">
            <div className="absolute inset-0 flex items-center">
              <div className="w-full border-t border-white/10" />
            </div>
            <div className="relative flex justify-center text-xs">
              <span className="px-3 bg-[#0a0a0f] text-white/30">or sign in with email</span>
            </div>
          </div>

          <form onSubmit={handleLogin} className="space-y-4">
            <div>
              <label className="text-sm font-medium mb-1.5 block text-white/70">Email</label>
              <Input type="email" placeholder="you@company.com" value={email} onChange={e => setEmail(e.target.value)} required
                className="bg-white/5 border-white/10 text-white placeholder:text-white/30 focus:border-indigo-500/50" />
            </div>
            <div>
              <div className="flex justify-between items-center mb-1.5">
                <label className="text-sm font-medium text-white/70">Password</label>
                <Link href="/forgot-password" className="text-sm text-indigo-400 hover:text-indigo-300 hover:underline transition-colors">Forgot password?</Link>
              </div>
              <Input type="password" placeholder="••••••••" value={password} onChange={e => setPassword(e.target.value)} required
                className="bg-white/5 border-white/10 text-white placeholder:text-white/30 focus:border-indigo-500/50" />
            </div>
            <Button type="submit" className="w-full bg-gradient-to-r from-indigo-500 to-purple-600 hover:from-indigo-600 hover:to-purple-700 text-white border-0 h-11 shadow-lg shadow-indigo-500/20" disabled={loading || googleLoading}>
              {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Sign In'}
            </Button>
          </form>

          <p className="text-center text-sm text-white/30 mt-6">
            Don&apos;t have an account?{' '}
            <Link href="/signup" className="text-indigo-400 hover:text-indigo-300 hover:underline font-medium transition-colors">Sign up</Link>
          </p>
        </div>
      </div>
    </div>
  );
}

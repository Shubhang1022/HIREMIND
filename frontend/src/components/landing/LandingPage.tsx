'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import {
  Brain, Target, BarChart3, MessageSquare, Shield, Sparkles,
  Upload, FileText, Cpu, ListOrdered, ArrowRight, Check, Star,
  Pencil, Trash2, Plus, X, Loader2, Users, Zap, Clock,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Textarea } from '@/components/ui/textarea';
import { Input } from '@/components/ui/input';
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '@/components/ui/accordion';
import { toast } from 'sonner';

// ── Types ────────────────────────────────────────────────────────────────────
interface Review {
  id: string;
  userId: string;
  userName: string;
  role: string;
  rating: number;
  text: string;
  createdAt: string;
  updatedAt: string;
}

// ── Cookie helpers ────────────────────────────────────────────────────────────
function getUserId(): string {
  if (typeof document === 'undefined') return '';
  const match = document.cookie.match(/hiremind_uid=([^;]+)/);
  if (match) return match[1];
  const id = 'user_' + Math.random().toString(36).slice(2, 11);
  document.cookie = `hiremind_uid=${id}; max-age=${60 * 60 * 24 * 365}; path=/`;
  return id;
}

// ── In-memory review store (persists during session) ─────────────────────────
let _reviews: Review[] = [];

// ── Static content ────────────────────────────────────────────────────────────
const features = [
  { icon: Brain, title: 'Candidate Intelligence', desc: 'Deep semantic understanding of resumes, profiles, and career narratives — not just keyword matching.' },
  { icon: ListOrdered, title: 'AI Ranking', desc: 'Multi-signal scoring ranks candidates by fit, experience, skills, and behavioral signals.' },
  { icon: Target, title: 'Skill Gap Analysis', desc: 'Instantly identify missing skills and competency gaps for every candidate against your JD.' },
  { icon: MessageSquare, title: 'Interview Copilot', desc: "AI-generated interview questions tailored to each candidate's profile and gaps." },
  { icon: BarChart3, title: 'Behavioral Insights', desc: 'Surface engagement patterns, career progression signals, and hiring readiness indicators.' },
  { icon: Shield, title: 'Integrity Detection', desc: 'Flag suspicious profiles, honeypots, and inconsistencies before they reach your shortlist.' },
];

const steps = [
  { num: '01', icon: Upload, title: 'Upload Candidates', desc: 'Drop CSV, Excel, JSON, PDF resumes, or entire folders. Any format works.' },
  { num: '02', icon: FileText, title: 'Add Job Description', desc: 'Paste or upload your JD. Gemini AI extracts requirements automatically.' },
  { num: '03', icon: Cpu, title: 'AI Analysis', desc: 'Semantic embeddings, multi-dimensional scoring, and explainable rankings in minutes.' },
  { num: '04', icon: Sparkles, title: 'Get Ranked Shortlist', desc: 'Review ranked candidates with scores, AI reasoning, and full profile details.' },
];

// const stats = [
//   { icon: Users, value: '50K+', label: 'Candidates Analyzed' },
//   { icon: Zap, value: '94%', label: 'Screening Time Saved' },
//   { icon: Clock, value: '<2 min', label: 'Average Time to Rank' },
//   { icon: Shield, value: '99.9%', label: 'Uptime SLA' },
// ];

const faqs = [
  { q: 'What file formats do you support?', a: 'CSV, XLSX, JSON, JSONL, TXT, PDF, and DOCX. You can also upload entire folders of resumes.' },
  { q: 'Do I need to map columns or define a schema?', a: 'No. HireMind automatically infers candidate fields from any dataset structure using AI-powered schema detection.' },
  { q: 'How does the AI ranking work?', a: 'We use semantic embeddings (BGE models) combined with multi-signal scoring across experience, skills, behavioral signals, and integrity checks. Gemini AI generates grounded reasoning for each ranked candidate.' },
  { q: 'Is my candidate data secure?', a: 'Yes. All data is encrypted at rest and in transit. Row-level security ensures complete tenant isolation via Supabase.' },
  { q: 'Can I export results?', a: 'Absolutely. Export ranked shortlists as CSV or JSON with full explainability data.' },
];

// ── Glass card style ──────────────────────────────────────────────────────────
const glass = 'backdrop-blur-xl bg-white/5 border border-white/10 shadow-xl shadow-black/20';
const glassDark = 'backdrop-blur-xl bg-black/20 border border-white/8 shadow-xl shadow-black/30';

// ── Review Card ───────────────────────────────────────────────────────────────
function ReviewCard({ review, userId, onEdit, onDelete }: {
  review: Review; userId: string;
  onEdit: (r: Review) => void; onDelete: (id: string) => void;
}) {
  const isOwner = review.userId === userId;
  const canEdit = isOwner && (Date.now() - new Date(review.createdAt).getTime()) < 24 * 60 * 60 * 1000;
  const initials = review.userName.split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase();

  return (
    <div className={`${glass} rounded-2xl p-6 flex flex-col gap-4`}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-full bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center text-white text-sm font-bold shrink-0">
            {initials}
          </div>
          <div>
            <p className="font-semibold text-sm">{review.userName}</p>
            <p className="text-xs text-muted-foreground">{review.role}</p>
          </div>
        </div>
        {isOwner && (
          <div className="flex gap-1 shrink-0">
            {canEdit && (
              <button onClick={() => onEdit(review)} className="p-1.5 rounded-lg hover:bg-white/10 transition-colors text-muted-foreground hover:text-indigo-300">
                <Pencil className="w-3.5 h-3.5" />
              </button>
            )}
            <button onClick={() => onDelete(review.id)} className="p-1.5 rounded-lg hover:bg-white/10 transition-colors text-muted-foreground hover:text-red-400">
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          </div>
        )}
      </div>
      <div className="flex gap-0.5">
        {Array.from({ length: 5 }).map((_, i) => (
          <Star key={i} className={`w-4 h-4 ${i < review.rating ? 'fill-yellow-400 text-yellow-400' : 'text-muted-foreground/30'}`} />
        ))}
      </div>
      <p className="text-sm text-muted-foreground leading-relaxed">&ldquo;{review.text}&rdquo;</p>
      <p className="text-xs text-muted-foreground/50">{new Date(review.createdAt).toLocaleDateString()}</p>
    </div>
  );
}

// ── Review Form ───────────────────────────────────────────────────────────────
function ReviewForm({ onSubmit, onCancel, initial }: {
  onSubmit: (data: { name: string; role: string; rating: number; text: string }) => void;
  onCancel: () => void;
  initial?: Partial<{ name: string; role: string; rating: number; text: string }>;
}) {
  const [name, setName] = useState(initial?.name || '');
  const [role, setRole] = useState(initial?.role || '');
  const [rating, setRating] = useState(initial?.rating || 5);
  const [text, setText] = useState(initial?.text || '');
  const [hover, setHover] = useState(0);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !text.trim()) { toast.error('Please fill in your name and review.'); return; }
    onSubmit({ name: name.trim(), role: role.trim() || 'Recruiter', rating, text: text.trim() });
  };

  return (
    <form onSubmit={handleSubmit} className={`${glass} rounded-2xl p-6 space-y-4`}>
      <div className="flex items-center justify-between">
        <h4 className="font-semibold">{initial ? 'Edit Review' : 'Leave a Review'}</h4>
        <button type="button" onClick={onCancel} className="p-1 rounded-lg hover:bg-white/10 text-muted-foreground">
          <X className="w-4 h-4" />
        </button>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Input placeholder="Your name *" value={name} onChange={e => setName(e.target.value)} className="bg-white/5 border-white/10" />
        <Input placeholder="Your role (optional)" value={role} onChange={e => setRole(e.target.value)} className="bg-white/5 border-white/10" />
      </div>
      <div className="flex items-center gap-2">
        <span className="text-sm text-muted-foreground">Rating:</span>
        <div className="flex gap-1">
          {Array.from({ length: 5 }).map((_, i) => (
            <button key={i} type="button"
              onMouseEnter={() => setHover(i + 1)} onMouseLeave={() => setHover(0)}
              onClick={() => setRating(i + 1)}
            >
              <Star className={`w-5 h-5 transition-colors ${i < (hover || rating) ? 'fill-yellow-400 text-yellow-400' : 'text-muted-foreground/40 hover:text-yellow-300'}`} />
            </button>
          ))}
        </div>
      </div>
      <Textarea
        placeholder="Share your experience with HireMind AI..."
        value={text} onChange={e => setText(e.target.value)}
        className="bg-white/5 border-white/10 min-h-[100px] resize-none"
      />
      <div className="flex gap-2 justify-end">
        <Button type="button" variant="ghost" size="sm" onClick={onCancel}>Cancel</Button>
        <Button type="submit" size="sm" className="bg-gradient-to-r from-indigo-500 to-purple-600 text-white border-0">
          {initial ? 'Save Changes' : 'Submit Review'}
        </Button>
      </div>
    </form>
  );
}

// ── Main Landing Page ─────────────────────────────────────────────────────────
export function LandingPage() {
  const [userId, setUserId] = useState('');
  const [reviews, setReviews] = useState<Review[]>(_reviews);
  const [showForm, setShowForm] = useState(false);
  const [editingReview, setEditingReview] = useState<Review | null>(null);

  useEffect(() => {
    setUserId(getUserId());
    setReviews([..._reviews]);
  }, []);

  const handleAddReview = (data: { name: string; role: string; rating: number; text: string }) => {
    const now = new Date().toISOString();
    const r: Review = { id: Math.random().toString(36).slice(2), userId, userName: data.name, role: data.role, rating: data.rating, text: data.text, createdAt: now, updatedAt: now };
    _reviews = [r, ..._reviews];
    setReviews([..._reviews]);
    setShowForm(false);
    toast.success('Review submitted!');
  };

  const handleEditReview = (data: { name: string; role: string; rating: number; text: string }) => {
    if (!editingReview) return;
    _reviews = _reviews.map(r => r.id === editingReview.id ? { ...r, ...data, updatedAt: new Date().toISOString() } : r);
    setReviews([..._reviews]);
    setEditingReview(null);
    toast.success('Review updated!');
  };

  const handleDeleteReview = (id: string) => {
    if (!confirm('Delete your review permanently?')) return;
    _reviews = _reviews.filter(r => r.id !== id);
    setReviews([..._reviews]);
    toast.success('Review deleted.');
  };

  const userReview = reviews.find(r => r.userId === userId);

  return (
    <div className="min-h-screen bg-[#050508]">
      {/* Ambient background */}
      <div className="fixed inset-0 pointer-events-none">
        <div className="absolute top-0 left-1/4 w-[600px] h-[600px] bg-indigo-600/15 rounded-full blur-[120px]" />
        <div className="absolute top-1/3 right-1/4 w-[500px] h-[500px] bg-purple-600/10 rounded-full blur-[120px]" />
        <div className="absolute bottom-1/4 left-1/3 w-[400px] h-[400px] bg-blue-600/8 rounded-full blur-[100px]" />
      </div>

      {/* Nav */}
      <nav className={`fixed top-0 inset-x-0 z-50 ${glassDark} border-b border-white/8`}>
        <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
          <Link href="/" className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center shadow-lg shadow-indigo-500/30">
              <Sparkles className="w-4 h-4 text-white" />
            </div>
            <span className="font-bold text-lg text-white">HireMind AI</span>
          </Link>
          <div className="hidden md:flex items-center gap-8 text-sm text-white/60">
            <a href="#features" className="hover:text-white transition-colors">Features</a>
            <a href="#how-it-works" className="hover:text-white transition-colors">How It Works</a>
            <a href="#reviews" className="hover:text-white transition-colors">Reviews</a>
            <a href="#faq" className="hover:text-white transition-colors">FAQ</a>
          </div>
          <div className="flex items-center gap-3">
            <Link href="/login"><Button variant="ghost" size="sm" className="text-white/70 hover:text-white hover:bg-white cursor-pointer">Sign In</Button></Link>
            <Link href="/signup"><Button size="sm" className="bg-gradient-to-r from-indigo-500 to-purple-600 text-white border-0 shadow-lg shadow-indigo-500/20 cursor-pointer">Start Hiring</Button></Link>
          </div>
        </div>
      </nav>

      {/* Hero */}
      <section className="relative pt-36 pb-24 px-6">
        <div className="max-w-5xl mx-auto text-center relative">
          <Badge variant="secondary" className="mb-6 px-4 py-1.5 text-sm bg-white/8 border-white/15 text-white/80">
            <Sparkles className="w-3.5 h-3.5 mr-1.5 inline text-indigo-400" /> Powered by  AI + Sentence Transformers
          </Badge>
          <h1 className="text-5xl md:text-7xl font-bold tracking-tight mb-6 text-white leading-[1.1]">
            AI Recruiter Copilot<br />
            <span className="bg-gradient-to-r from-indigo-400 via-purple-400 to-pink-400 bg-clip-text text-transparent">
              for Smarter Hiring
            </span>
          </h1>
          <p className="text-xl text-white/50 max-w-3xl mx-auto mb-10 leading-relaxed">
            Analyze candidates beyond keywords. Rank talent using semantic understanding, behavioral intelligence, and explainable AI reasoning.
          </p>
          <div className="flex flex-col sm:flex-row gap-4 justify-center">
            <Link href="/signup">
              <Button size="lg" className="bg-gradient-to-r from-indigo-500 to-purple-600 hover:from-indigo-600 hover:to-purple-700 text-white border-0 px-8 h-12 text-base shadow-xl shadow-indigo-500/25 cursor-pointer">
                Start Hiring Free <ArrowRight className="ml-2 w-4 h-4" />
              </Button>
            </Link>
            <Link href="/login">
              <Button size="lg" variant="outline" className="h-12 px-8 text-base border-white/15 text-white/80 hover:bg-white/10 hover:text-white cursor-pointer">
                Sign In to Dashboard
              </Button>
            </Link>
          </div>

          {/* Stats */}
         
           
         

          {/* Feature showcase replacing demo ranking board */}
          <div className={`mt-12 ${glass} rounded-2xl p-6 md:p-8 text-left`}>
            <div className="flex items-center gap-2 mb-6">
              <div className="w-2.5 h-2.5 rounded-full bg-green-400 animate-pulse" />
              <span className="text-sm text-white/50 font-mono">live analysis · 6-dimension scoring</span>
            </div>
            <div className="grid md:grid-cols-3 gap-4">
              {[
                { dim: 'Semantic Fit', score: 0.92, color: 'from-indigo-500 to-indigo-400', desc: 'Embedding similarity vs JD' },
                { dim: 'Experience Quality', score: 0.88, color: 'from-purple-500 to-purple-400', desc: 'Product co. + production ML' },
                { dim: 'Career Progression', score: 0.75, color: 'from-blue-500 to-blue-400', desc: 'Trajectory + leadership signals' },
                { dim: 'Behavioral Signals', score: 0.82, color: 'from-emerald-500 to-emerald-400', desc: 'Hiring readiness + engagement' },
                { dim: 'Logistics Fit', score: 1.0, color: 'from-amber-500 to-amber-400', desc: 'Location + notice + salary' },
                { dim: 'Profile Integrity', score: 0.90, color: 'from-rose-500 to-rose-400', desc: 'Verification + consistency' },
              ].map(d => (
                <div key={d.dim} className="space-y-2">
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-white/70 font-medium">{d.dim}</span>
                    <span className="text-white font-bold">{Math.round(d.score * 100)}%</span>
                  </div>
                  <div className="h-1.5 rounded-full bg-white/10 overflow-hidden">
                    <div className={`h-full rounded-full bg-gradient-to-r ${d.color} transition-all duration-1000`} style={{ width: `${d.score * 100}%` }} />
                  </div>
                  <p className="text-xs text-white/30">{d.desc}</p>
                </div>
              ))}
            </div>
            <div className="mt-6 pt-4 border-t border-white/8 flex items-center justify-between">
              <div className="text-sm text-white/40">Final Score</div>
              <div className="text-2xl font-bold bg-gradient-to-r from-indigo-400 to-purple-400 bg-clip-text text-transparent">89.3%</div>
            </div>
          </div>
        </div>
      </section>

      {/* Features */}
      <section id="features" className="py-24 px-6">
        <div className="max-w-7xl mx-auto">
          <div className="text-center mb-16">
            <h2 className="text-3xl md:text-4xl font-bold text-white mb-4">Everything you need to hire smarter</h2>
            <p className="text-white/40 text-lg max-w-2xl mx-auto">From ingestion to interview — one platform for the entire hiring intelligence workflow.</p>
          </div>
          <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-5">
            {features.map((f, i) => (
              <div key={f.title} className={`group ${glass} rounded-2xl p-6 hover:border-indigo-500/30 hover:bg-white/8 transition-all duration-300`}>
                <div className="w-10 h-10 rounded-xl bg-indigo-500/15 flex items-center justify-center mb-4 group-hover:bg-indigo-500/25 transition-colors border border-indigo-500/20">
                  <f.icon className="w-5 h-5 text-indigo-400" />
                </div>
                <h3 className="font-semibold text-white mb-2">{f.title}</h3>
                <p className="text-white/40 text-sm leading-relaxed">{f.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* How It Works */}
      <section id="how-it-works" className="py-24 px-6">
        <div className="max-w-7xl mx-auto">
          <div className="text-center mb-16">
            <h2 className="text-3xl md:text-4xl font-bold text-white mb-4">How it works</h2>
            <p className="text-white/40 text-lg">Four steps from raw data to ranked shortlist.</p>
          </div>
          <div className="flex flex-col md:flex-row items-stretch gap-0">
            {steps.map((s, i) => (
              <div key={s.num} className="flex flex-col md:flex-row items-stretch flex-1 gap-0">
                <div className={`${glass} rounded-2xl p-6 text-center flex-1`}>
                  <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-indigo-500/20 to-purple-500/20 border border-indigo-500/20 flex items-center justify-center mx-auto mb-4">
                    <s.icon className="w-6 h-6 text-indigo-400" />
                  </div>
                  <div className="text-xs font-mono text-indigo-400/70 mb-2">{s.num}</div>
                  <h3 className="font-semibold text-white mb-2">{s.title}</h3>
                  <p className="text-sm text-white/40">{s.desc}</p>
                </div>
                {i < steps.length - 1 && (
                  <div className="hidden md:flex items-center justify-center px-2 shrink-0">
                    <ArrowRight className="w-5 h-5 text-indigo-400/40" />
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Reviews */}
      <section id="reviews" className="py-24 px-6">
        <div className="max-w-7xl mx-auto">
          <div className="flex flex-col sm:flex-row sm:items-end justify-between mb-12 gap-4">
            <div>
              <h2 className="text-3xl md:text-4xl font-bold text-white mb-2">What our users say</h2>
              <p className="text-white/40">Real reviews from recruiting teams. Your review stays yours — edit within 24h, delete anytime.</p>
            </div>
            {!userReview && !showForm && (
              <Button onClick={() => setShowForm(true)} className="bg-gradient-to-r from-indigo-500 to-purple-600 text-white border-0 shrink-0">
                <Plus className="w-4 h-4 mr-2" /> Leave a Review
              </Button>
            )}
          </div>

          {/* Review form */}
          {showForm && !editingReview && (
            <div className="mb-8 max-w-2xl">
              <ReviewForm onSubmit={handleAddReview} onCancel={() => setShowForm(false)} />
            </div>
          )}

          {reviews.length === 0 ? (
            <div className={`${glass} rounded-2xl p-12 text-center`}>
              <Star className="w-12 h-12 text-white/20 mx-auto mb-4" />
              <p className="text-white/40 mb-4">No reviews yet. Be the first to share your experience!</p>
              {!showForm && (
                <Button onClick={() => setShowForm(true)} variant="outline" className="border-white/15 text-white/70 hover:bg-white/10">
                  <Plus className="w-4 h-4 mr-2" /> Write a Review
                </Button>
              )}
            </div>
          ) : (
            <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-5">
              {reviews.map(r => (
                editingReview?.id === r.id ? (
                  <ReviewForm key={r.id} initial={{ name: r.userName, role: r.role, rating: r.rating, text: r.text }}
                    onSubmit={handleEditReview} onCancel={() => setEditingReview(null)} />
                ) : (
                  <ReviewCard key={r.id} review={r} userId={userId}
                    onEdit={setEditingReview} onDelete={handleDeleteReview} />
                )
              ))}
            </div>
          )}

          {userReview && !showForm && (
            <p className="text-center text-sm text-white/30 mt-6">
              You&apos;ve already left a review.
              {Date.now() - new Date(userReview.createdAt).getTime() < 24 * 60 * 60 * 1000
                ? ' You can edit it within 24 hours of posting.'
                : ' Editing period has expired, but you can delete it anytime.'}
            </p>
          )}
        </div>
      </section>

      {/* FAQ */}
      <section id="faq" className="py-24 px-6">
        <div className="max-w-3xl mx-auto">
          <h2 className="text-3xl md:text-4xl font-bold text-white text-center mb-12">Frequently asked questions</h2>
          <Accordion className="space-y-3">
            {faqs.map((f, i) => (
              <AccordionItem key={i} value={`faq-${i}`} className={`${glass} rounded-xl px-5 border-white/8`}>
                <AccordionTrigger className="text-left hover:no-underline text-white/80 hover:text-white">{f.q}</AccordionTrigger>
                <AccordionContent className="text-white/40">{f.a}</AccordionContent>
              </AccordionItem>
            ))}
          </Accordion>
        </div>
      </section>

      {/* CTA */}
      <section className="py-24 px-6">
        <div className="max-w-4xl mx-auto text-center">
          <div className={`${glass} rounded-3xl p-12 relative overflow-hidden`}>
            <div className="absolute inset-0 bg-gradient-to-br from-indigo-500/10 to-purple-500/10 rounded-3xl" />
            <div className="relative">
              <h2 className="text-3xl font-bold text-white mb-4">Ready to transform your hiring?</h2>
              <p className="text-white/40 mb-8 text-lg">Join recruiting teams using AI to find the best talent faster.</p>
              <Link href="/signup">
                <Button size="lg" className="bg-gradient-to-r from-indigo-500 to-purple-600 text-white border-0 px-8 shadow-xl shadow-indigo-500/25">
                  Get Started Free <ArrowRight className="ml-2 w-4 h-4" />
                </Button>
              </Link>
            </div>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className={`border-t border-white/8 py-10 px-6`}>
        <div className="max-w-7xl mx-auto flex flex-col md:flex-row justify-between items-center gap-6">
          <div className="flex items-center gap-2">
            <div className="w-7 h-7 rounded-md bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center">
              <Sparkles className="w-3.5 h-3.5 text-white" />
            </div>
            <span className="font-semibold text-white">HireMind AI</span>
          </div>
          <p className="text-sm text-white/30">&copy; {new Date().getFullYear()} HireMind AI. All rights reserved.</p>
          <div className="flex gap-6 text-sm text-white/30">
            <a href="#" className="hover:text-white/70 transition-colors">Privacy</a>
            <a href="#" className="hover:text-white/70 transition-colors">Terms</a>
            <a href="#" className="hover:text-white/70 transition-colors">Contact</a>
          </div>
        </div>
      </footer>
    </div>
  );
}

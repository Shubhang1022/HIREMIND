export interface Candidate {
  id: string;
  rank: number;
  name: string;
  role: string;
  company: string;
  experience: number;
  location: string;
  availability: string;
  aiScore: number;
  matchPercent: number;
  integrityScore: number;
  confidenceScore: number;
  hiringReadiness: number;
  behavioralFit: number;
  matchStatus: 'excellent' | 'strong' | 'moderate' | 'weak';
  summary: string;
  skills: { name: string; category: string; proficiency: number }[];
  timeline: { title: string; company: string; startDate: string; endDate: string; type: 'promotion' | 'change' }[];
  strengths: string[];
  weaknesses: string[];
  whySelected: string[];
  whyNotSelected: string[];
  risks: string[];
  missingSkills: string[];
  interviewQuestions: { category: string; questions: string[] }[];
}

export const mockCandidates: Candidate[] = [
  {
    id: '1',
    rank: 1,
    name: 'Priya Sharma',
    role: 'Senior AI Engineer',
    company: 'Redrob AI',
    experience: 7,
    location: 'Pune, India',
    availability: 'Immediate',
    aiScore: 94,
    matchPercent: 92,
    integrityScore: 98,
    confidenceScore: 96,
    hiringReadiness: 90,
    behavioralFit: 88,
    matchStatus: 'excellent',
    summary: 'Strong retrieval engineer with production FAISS + BGE deployment, NDCG evaluation, and vector DB experience. Open to work with 15-day notice. Excellent fit for founding-team Senior AI Engineer.',
    skills: [
      { name: 'Python', category: 'Programming', proficiency: 95 },
      { name: 'Machine Learning', category: 'AI/ML', proficiency: 92 },
      { name: 'Retrieval Systems', category: 'AI/ML', proficiency: 90 },
      { name: 'TensorFlow', category: 'AI/ML', proficiency: 85 },
      { name: 'PyTorch', category: 'AI/ML', proficiency: 88 },
    ],
    timeline: [
      { title: 'Senior AI Engineer', company: 'TechFlow AI', startDate: '2022-01', endDate: 'Present', type: 'change' },
      { title: 'AI Engineer', company: 'TechFlow AI', startDate: '2020-03', endDate: '2022-01', type: 'promotion' },
      { title: 'ML Engineer', company: 'DataCore Inc', startDate: '2018-06', endDate: '2020-03', type: 'change' },
    ],
    strengths: ['Production retrieval systems', 'NDCG/MRR evaluation', 'Vector DB + embeddings'],
    weaknesses: ['Limited edge deployment experience'],
    whySelected: ['Strong semantic match (92%)', 'Production FAISS + BGE deployment', 'Actively open to work'],
    whyNotSelected: [],
    risks: [],
    missingSkills: ['Rust'],
    interviewQuestions: [
      { category: 'Technical', questions: ['Explain your experience with RAG systems.', 'How do you optimize retrieval performance?'] },
      { category: 'System Design', questions: ['Design a scalable ranking system.'] },
    ],
  },
  {
    id: '2',
    rank: 2,
    name: 'Arjun Mehta',
    role: 'Senior ML Engineer',
    company: 'FinScale AI',
    experience: 8,
    location: 'Hyderabad, India',
    availability: '30 days',
    aiScore: 89,
    matchPercent: 87,
    integrityScore: 95,
    confidenceScore: 91,
    hiringReadiness: 85,
    behavioralFit: 82,
    matchStatus: 'strong',
    summary: 'Product-company ML engineer with Weaviate hybrid search, offline evaluation, and model serving at scale. Strong leadership on ranking systems.',
    skills: [
      { name: 'Python', category: 'Programming', proficiency: 90 },
      { name: 'Weaviate', category: 'AI/ML', proficiency: 88 },
      { name: 'MLOps', category: 'DevOps', proficiency: 85 },
      { name: 'NDCG', category: 'AI/ML', proficiency: 82 },
    ],
    timeline: [
      { title: 'Senior ML Engineer', company: 'FinScale AI', startDate: '2021-05', endDate: 'Present', type: 'change' },
      { title: 'ML Engineer', company: 'FinScale AI', startDate: '2019-08', endDate: '2021-05', type: 'promotion' },
    ],
    strengths: ['Hybrid search', 'Production serving', 'Team leadership'],
    weaknesses: ['Less LLM fine-tuning exposure'],
    whySelected: ['Vector DB + evaluation frameworks', 'Mostly product-company experience', '30-day notice'],
    whyNotSelected: ['Missing explicit BGE/sentence-transformers mention'],
    risks: [],
    missingSkills: ['LoRA/QLoRA fine-tuning'],
    interviewQuestions: [
      { category: 'Technical', questions: ['How did you correlate offline NDCG with online metrics?'] },
      { category: 'Leadership', questions: ['Describe leading a cross-functional ranking launch.'] },
    ],
  },
  {
    id: '3',
    rank: 3,
    name: 'Neha Kapoor',
    role: 'AI Engineer',
    company: 'SearchFirst',
    experience: 6,
    location: 'Bangalore, India',
    availability: '45 days',
    aiScore: 85,
    matchPercent: 80,
    integrityScore: 90,
    confidenceScore: 85,
    hiringReadiness: 75,
    behavioralFit: 70,
    matchStatus: 'strong',
    summary: 'Retrieval-focused engineer with Pinecone + sentence-transformers in production. Good progression; verify depth on evaluation frameworks.',
    skills: [
      { name: 'Retrieval', category: 'AI/ML', proficiency: 92 },
      { name: 'Python', category: 'Programming', proficiency: 88 },
      { name: 'Pinecone', category: 'AI/ML', proficiency: 86 },
    ],
    timeline: [
      { title: 'AI Engineer', company: 'SearchFirst', startDate: '2020-01', endDate: 'Present', type: 'change' },
    ],
    strengths: ['Semantic search', 'Embedding pipelines'],
    weaknesses: ['Limited A/B testing evidence'],
    whySelected: ['Embeddings + vector DB in production', '6–8 year experience sweet spot'],
    whyNotSelected: ['Longer notice period (45 days)'],
    risks: ['Validate evaluation framework depth in interview'],
    missingSkills: ['Offline→online evaluation correlation'],
    interviewQuestions: [
      { category: 'Technical', questions: ['Walk through your Pinecone indexing and refresh strategy.'] },
    ],
  },
  {
    id: '4',
    rank: 4,
    name: 'Rahul Verma',
    role: 'Marketing Manager',
    company: 'RetailCo',
    experience: 5,
    location: 'Mumbai, India',
    availability: 'Immediate',
    aiScore: 42,
    matchPercent: 38,
    integrityScore: 55,
    confidenceScore: 40,
    hiringReadiness: 60,
    behavioralFit: 45,
    matchStatus: 'weak',
    summary: 'Non-technical profile with inflated AI buzzwords but weak production evidence. Flagged for keyword stuffing — not recommended for top 100.',
    skills: [
      { name: 'LangChain', category: 'AI/ML', proficiency: 90 },
      { name: 'RAG', category: 'AI/ML', proficiency: 88 },
      { name: 'ChatGPT', category: 'AI/ML', proficiency: 85 },
    ],
    timeline: [
      { title: 'Marketing Manager', company: 'RetailCo', startDate: '2021-06', endDate: 'Present', type: 'change' },
    ],
    strengths: ['Communication'],
    weaknesses: ['No production ML evidence', 'Title–skill mismatch'],
    whySelected: [],
    whyNotSelected: ['Non-technical career with no AI history', 'Keyword stuffing detected', 'Low semantic fit (38%)'],
    risks: ['Potential keyword stuffing — verify all ML claims', 'Profile integrity below threshold'],
    missingSkills: ['Python', 'Vector DB', 'Production ML', 'Evaluation frameworks'],
    interviewQuestions: [
      { category: 'Verification', questions: ['Describe a production ML system you personally built and deployed.'] },
    ],
  },
];

export const dashboardMetrics = {
  totalCandidates: 100000,
  rankedCandidates: 91580,
  topMatches: 142,
  activeJobs: 1,
};

export const qualityTrendData = [
  { month: 'Jan', quality: 72 },
  { month: 'Feb', quality: 75 },
  { month: 'Mar', quality: 78 },
  { month: 'Apr', quality: 80 },
  { month: 'May', quality: 82 },
  { month: 'Jun', quality: 85 },
];

export const hiringFunnelData = [
  { stage: 'Applied', count: 12453 },
  { stage: 'Screened', count: 5234 },
  { stage: 'Interviewed', count: 1245 },
  { stage: 'Offer', count: 234 },
  { stage: 'Hired', count: 45 },
];

export const matchDistributionData = [
  { name: 'Excellent', value: 142, color: '#10b981' },
  { name: 'Strong', value: 1234, color: '#3b82f6' },
  { name: 'Moderate', value: 3456, color: '#f59e0b' },
  { name: 'Weak', value: 3402, color: '#ef4444' },
];

export const aiInsights = [
  { title: 'Top Skill Gap', value: 'Vector DB', trend: 'Critical' },
  { title: 'Avg Top-100 Score', value: '0.72', trend: '+8%' },
  { title: 'Honeypots Blocked', value: '82', trend: 'Protected' },
  { title: 'Open to Work', value: '36%', trend: 'Active pool' },
];

export const recruiterActivity = [
  { name: 'Profile Views', value: 2341, change: '+18%' },
  { name: 'Messages Sent', value: 452, change: '+22%' },
  { name: 'Interviews Scheduled', value: 89, change: '+15%' },
  { name: 'Offers Made', value: 12, change: '+5%' },
];

export const hiddenGems = [mockCandidates[2]];
export const highRisk = [mockCandidates[3]];

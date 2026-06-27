const { createClient } = require('@supabase/supabase-js');
require('dotenv').config({ path: '.env.local' });

const supabaseUrl = process.env.VITE_SUPABASE_URL;
const supabaseAnonKey = process.env.VITE_SUPABASE_ANON_KEY;

const supabase = createClient(supabaseUrl, supabaseAnonKey);

async function check() {
  const { data, error } = await supabase.from('agents').select('*').limit(1);
  if (error) {
    console.log("Error querying 'agents':", error.message);
  } else {
    console.log("'agents' table exists. Sample:", data);
  }

  const { data: d2, error: e2 } = await supabase.from('activity_log').select('*').limit(1);
  if (e2) {
    console.log("Error querying 'activity_log':", e2.message);
  } else {
    console.log("'activity_log' table exists. Sample:", d2);
  }

  const { data: d4, error: e4 } = await supabase.from('agent_runs').select('*').limit(1);
  if (e4) {
    console.log("Error querying 'agent_runs':", e4.message);
  } else {
    console.log("'agent_runs' table exists. Sample:", d4);
  }
}
check();

const { createClient } = require('@supabase/supabase-js');
require('dotenv').config({ path: '.env.local' });

const supabaseUrl = process.env.VITE_SUPABASE_URL;
const supabaseAnonKey = process.env.VITE_SUPABASE_ANON_KEY;

const supabase = createClient(supabaseUrl, supabaseAnonKey);

async function check() {
  const { data, error } = await supabase.from('agent_activity').select('*').limit(1);
  if (error) {
    console.log("Error or table doesn't exist:", error);
    // Try another table name
    const { data: d2, error: e2 } = await supabase.from('activity_log').select('*').limit(1);
    console.log("Try activity_log:", e2 ? e2 : d2);
  } else {
    console.log("agent_activity table exists:", data);
  }
}
check();

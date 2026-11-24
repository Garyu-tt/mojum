const fs=require('node:fs');
const assert=require('node:assert/strict');
const html=fs.readFileSync('docs/roadmap/index.html','utf8');
const js=html.match(/<script>([\s\S]*?)<\/script>/)[1].split("$('view').addEventListener")[0];
new Function('assert',js+String.raw`
data={issues:[{number:1,title:'GENESIS <test>',html_url:'https://github.com/Garyu-tt/mojum/issues/1',state:'open',created_at:'2026-11-01',body:'See #2'},{number:2,title:'PR #1',html_url:'https://github.com/Garyu-tt/mojum/pull/2',state:'closed',created_at:'2026-11-01',closed_at:'2026-11-02',pull_request:{merged_at:'2026-11-02'}}],pulls:[{number:2,merged_at:'2026-11-02',head:{ref:'feature/a'},base:{ref:'main'}}],releases:[]};
imported=[{id:'a',title:'GENESIS <test>',content:{url:'https://github.com/Garyu-tt/mojum/issues/1'}},{id:'b',title:'Draft task',content:{type:'DraftIssue'}}];
normalize();
assert.equal(items.length,3);
const issue=items.find(x=>x.number===1),pr=items.find(x=>x.number===2);
assert.ok(issue.project);assert.equal(references(issue)[0],2);assert.equal(pr.state,'merged');assert.equal(pr.base,'main');
assert.ok(list([issue]).includes('&lt;test&gt;'));
stamp='2026-11-03';assert.ok(graph([pr]).includes('feature/a'));
const cs=[{sha:'aaa',parents:[{sha:'bbb'},{sha:'ccc'}],commit:{message:'Merge'}},{sha:'bbb',parents:[],commit:{}},{sha:'ccc',parents:[],commit:{}}];
assert.ok(commitGraph(cs,cs).includes('MERGE'));assert.ok(commitGraph(cs.slice(0,1),cs).includes('stroke-dasharray'));
`)(assert);
console.log('5 roadmap fixture checks passed');

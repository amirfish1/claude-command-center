// CCC site behavior. No deps.
(function(){
  // mobile nav
  var t=document.querySelector('.nav-toggle');
  if(t)t.addEventListener('click',function(){document.body.classList.toggle('nav-open')});
  // scroll reveal
  var io=new IntersectionObserver(function(es){
    es.forEach(function(e){if(e.isIntersecting){e.target.classList.add('is-visible');io.unobserve(e.target)}})
  },{threshold:.12});
  document.querySelectorAll('.reveal').forEach(function(el){io.observe(el)});
  // github stars (graceful fallback to whatever text is already in the element)
  var el=document.querySelector('[data-gh-stars]');
  if(el){fetch('https://api.github.com/repos/amirfish1/claude-command-center')
    .then(function(r){return r.json()}).then(function(d){
      if(d&&typeof d.stargazers_count==='number'){
        el.textContent=Intl.NumberFormat().format(d.stargazers_count);
      }}).catch(function(){});}
  // copy buttons: <button data-copy="text to copy">
  document.querySelectorAll('[data-copy]').forEach(function(btn){
    btn.addEventListener('click',function(){
      var text=btn.getAttribute('data-copy')||'';
      var done=function(){
        var old=btn.textContent;
        btn.textContent='Copied!';
        btn.classList.add('copied-flash');
        setTimeout(function(){btn.textContent=old;btn.classList.remove('copied-flash')},1600);
      };
      if(navigator.clipboard&&navigator.clipboard.writeText){
        navigator.clipboard.writeText(text).then(done).catch(function(){fallback()});
      }else{fallback()}
      function fallback(){
        var ta=document.createElement('textarea');
        ta.value=text;ta.style.position='fixed';ta.style.opacity='0';
        document.body.appendChild(ta);ta.select();
        try{document.execCommand('copy')}catch(e){}
        document.body.removeChild(ta);done();
      }
    });
  });
})();

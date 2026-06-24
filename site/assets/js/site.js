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
})();
